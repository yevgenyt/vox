// Resident Parakeet worker: loads the model once, then transcribes one audio
// file per stdin line. parakeet-cli reloads the model on every run, and on the
// Radeon 890M (UMA) that load takes ~1 s when GPU memory is free but ~18 s when
// another process has bloated VRAM. Keeping the model loaded removes it.
//
// Protocol (stdout, one line per request, then flush):
//   READY                 once, after the model is loaded
//   OK\t<text>            transcription (newlines folded to spaces)
//   ERR\t<message>        the request failed; the worker keeps running
// stdin EOF exits. All library logging is suppressed so stdout stays clean.

#include "parakeet.h"
#include "common-whisper.h"

#include <cstdio>
#include <iostream>
#include <string>
#include <vector>

static void cb_log_disable(enum ggml_log_level, const char *, void *) {}

static void token_callback(parakeet_context * ctx, parakeet_state *, const parakeet_token_data * token_data, void * user_data) {
    auto * out = (std::string *) user_data;
    const char * token_str = parakeet_token_to_str(ctx, token_data->id);
    char text_buf[256];
    parakeet_token_to_text(token_str, out->empty(), text_buf, sizeof(text_buf));
    *out += text_buf;
}

static void reply(const char * status, std::string text) {
    for (char & c : text) {
        if (c == '\n' || c == '\r' || c == '\t') c = ' ';
    }
    printf("%s\t%s\n", status, text.c_str());
    fflush(stdout);
}

int main(int argc, char ** argv) {
    ggml_backend_load_all();
    parakeet_log_set(cb_log_disable, NULL);

    std::string model = "models/ggml-parakeet-tdt-0.6b-v3.bin";
    int n_threads = 4;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if ((arg == "-m" || arg == "--model") && i + 1 < argc) model = argv[++i];
        else if ((arg == "-t" || arg == "--threads") && i + 1 < argc) n_threads = std::stoi(argv[++i]);
    }

    struct parakeet_context_params ctx_params = parakeet_context_default_params();
    struct parakeet_context * pctx = parakeet_init_from_file_with_params(model.c_str(), ctx_params);
    if (pctx == nullptr) {
        reply("ERR", "failed to load model " + model);
        return 1;
    }

    printf("READY\n");
    fflush(stdout);

    std::string fname;
    while (std::getline(std::cin, fname)) {
        if (fname.empty()) continue;

        std::vector<float> pcmf32;
        std::vector<std::vector<float>> pcmf32s;
        if (!read_audio_data(fname.c_str(), pcmf32, pcmf32s, false)) {
            reply("ERR", "failed to read audio file " + fname);
            continue;
        }
        if (pcmf32.empty()) {
            reply("ERR", "no audio data in " + fname);
            continue;
        }

        std::string text;
        struct parakeet_full_params full_params = parakeet_full_default_params(PARAKEET_SAMPLING_GREEDY);
        full_params.n_threads = n_threads;
        full_params.new_token_callback = token_callback;
        full_params.new_token_callback_user_data = &text;

        if (parakeet_full(pctx, full_params, pcmf32.data(), pcmf32.size()) != 0) {
            reply("ERR", "failed to process audio file " + fname);
            continue;
        }
        reply("OK", text);
    }

    parakeet_free(pctx);
    return 0;
}
