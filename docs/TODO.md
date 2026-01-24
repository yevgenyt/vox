# Vox - Project TODO List

Last updated: 2026-01-24

## Critical

None at this time.

## High Priority

### 1. Stabilize and Enable Systemd Service
**Status**: Ready but disabled
**Location**: `~/.config/systemd/user/vox.service`
**Dependencies**: None (code is complete)
**Description**: Once the client proves stable in daily use, enable the systemd service for automatic startup on login.

**Action**:
```bash
systemctl --user enable --now vox
```

**Blocked By**: Need sufficient testing in production use to confirm stability.

## Medium Priority

### 2. Reconcile Duplicate LESSONS.md Files
**Status**: Pending review
**Location**:
- `/LESSONS.md` (root, main project)
- `/export-client/LESSONS.md` (standalone client)

**Issue**: The two LESSONS.md files have overlapping content but different scopes:
- Root `LESSONS.md` is project-wide (server + client)
- `export-client/LESSONS.md` is client-specific for standalone distribution

**Action Required**:
- Determine if `export-client/` is still maintained separately or if it should be removed
- If export-client is for standalone distribution, keep both and ensure they're synchronized where content overlaps
- If export-client is obsolete, remove it and consolidate all lessons in root `LESSONS.md`

### 3. Document Desktop Entry and Systemd Service Setup
**Status**: Files exist but not documented
**Location**:
- `~/.local/share/applications/vox.desktop`
- `~/.config/systemd/user/vox.service`

**Issue**: PROJECT.md and README.md mention these files but don't provide:
- Installation instructions
- Template files in the repository
- Configuration details

**Action Required**:
- Create template files in `/client/config/` or `/config/`:
  - `vox.service.template`
  - `vox.desktop.template`
- Add installation section to README.md with setup instructions
- Document systemd service management commands

### 4. Add Model Selection Documentation
**Status**: Missing user-facing documentation
**Location**: PROJECT.md mentions models, but no user guide

**Issue**: PROJECT.md describes model options (`ggml-small.en.bin` vs `ggml-small.bin`) but doesn't explain:
- How users can switch models
- Performance/accuracy tradeoffs
- Where to download additional models
- How to configure the server to use different models

**Action Required**:
- Add "Model Selection" section to README.md or docs/
- Document server configuration for model selection
- Provide download links for models
- Add performance benchmarks if available

## Low Priority

### 5. Add Testing Documentation
**Status**: No test documentation exists
**Issue**: No information about:
- How to test the server independently
- How to test the client independently
- Integration testing
- Audio quality validation

**Action Required**:
- Create `/docs/TESTING.md` with:
  - Manual testing procedures
  - Test audio files or how to generate them
  - Expected behavior validation
  - Troubleshooting common issues

### 6. Performance Metrics Documentation
**Status**: Missing
**Issue**: No documentation of:
- Expected transcription latency
- GPU vs CPU performance comparison
- Audio length vs processing time benchmarks
- Memory usage

**Action Required**:
- Add performance benchmarks to documentation
- Document expected system requirements for different use cases

### 7. Add Changelog
**Status**: Missing
**Issue**: No CHANGELOG.md to track version history and feature additions

**Action Required**:
- Create `/CHANGELOG.md`
- Document version history from git commits
- Establish versioning scheme (semantic versioning recommended)

## Backlog / Future Enhancements

### 8. Consider Multi-Model Support
**Status**: Enhancement idea
**Description**: Allow runtime model selection via API parameter

**Example**:
```bash
curl -X POST "http://localhost:5000/transcribe?model=small.en" \
  -F "audio=@recording.wav"
```

**Benefits**:
- English-only model for faster English transcription
- Multilingual model when language detection needed
- Future support for larger models for accuracy

### 9. Add Transcription Confidence Score
**Status**: Enhancement idea
**Description**: Include whisper.cpp confidence metrics in API response

**Benefit**: Help clients determine if transcription quality is acceptable

### 10. Stream Progress for Long Audio Files
**Status**: Enhancement idea
**Description**: For audio files longer than 30 seconds, provide progress updates

**Implementation**: WebSocket or Server-Sent Events

### 11. Add Audio Format Validation
**Status**: Enhancement idea
**Description**: Validate audio format before processing and return clear error messages

**Current behavior**: Server may fail with generic error on unsupported formats
**Desired behavior**: Return specific error like "Unsupported audio format: .xyz"

## Completed

### Export Client Status
**Decision Needed**: Determine if `/export-client/` directory should be:
1. Maintained as standalone redistributable client
2. Merged into `/client/`
3. Removed as obsolete

**Context**: Currently contains duplicate client code and documentation. If this is meant as a portable distribution package, it should be clearly documented. If not, it creates maintenance burden.

---

## Documentation Health

### Current State: GOOD

#### Strengths
1. Comprehensive PROJECT.md with architecture and knowledge transfer
2. Clear README.md with quick start and usage
3. Excellent LESSONS.md documenting development gotchas
4. Good API documentation in docs/API-INTEGRATION.md
5. Consistent formatting and structure

#### Areas for Improvement
1. Missing template configuration files (systemd, desktop entry)
2. Duplicate LESSONS.md needs reconciliation
3. export-client purpose unclear
4. No testing documentation
5. No changelog

### File Organization: GOOD

Current structure is logical and well-organized:
```
/
├── PROJECT.md              # Architecture & knowledge transfer
├── README.md               # User guide
├── LESSONS.md              # Development lessons
├── docs/
│   ├── API-INTEGRATION.md  # External client API guide
│   └── TODO.md             # This file
├── server/                 # Server code
├── client/                 # Client code
├── docker/                 # Container definitions
├── n8n/                    # Integration examples
└── export-client/          # ??? Needs clarification
```

### Recommendations

1. **Immediate**: Clarify export-client purpose or remove it
2. **Short-term**: Add config templates and installation docs
3. **Medium-term**: Add testing and performance documentation
4. **Long-term**: Establish changelog and versioning practices

---

## Notes

- All TODOs extracted from PROJECT.md Status section
- No TODOs found in code comments (clean codebase)
- Documentation is comprehensive and well-maintained
- Primary need is operational documentation (testing, deployment, monitoring)
