from __future__ import annotations

import random
import subprocess
from pathlib import Path


def _run_hidden_go_test(candidate: Path, seed: int) -> None:
    rng = random.Random(seed)
    message = f"hidden-{rng.getrandbits(64):016x}"
    event = f"evt-{rng.getrandbits(40):010x}"
    invalid_choice = f"invalid-{rng.getrandbits(32):08x}"
    cursor = rng.randint(1, 5)
    frame_count = rng.randint(cursor + 3, cursor + 12)

    source = f'''package handlers

import (
    "bytes"
    "context"
    "encoding/json"
    "fmt"
    "io"
    "net/http"
    "net/http/httptest"
    "path/filepath"
    "testing"

    "github.com/gin-gonic/gin"

    "github.com/didlawowo/keryx/pkg/hermes"
    "github.com/didlawowo/keryx/pkg/store"
)

// Everything prefixed workflowCITrusted is owned by the central oracle.
// The hidden evaluator must not depend on helpers from candidate *_test.go files.
type workflowCITrustedHermes struct {{
    events  []hermes.Event
    lastReq hermes.StreamRequest
}}

func (f *workflowCITrustedHermes) Stream(
    _ context.Context,
    req hermes.StreamRequest,
    onEvent func(hermes.Event) error,
) error {{
    f.lastReq = req
    for _, ev := range f.events {{
        if err := onEvent(ev); err != nil {{
            return err
        }}
    }}
    return nil
}}

func (f *workflowCITrustedHermes) Models(context.Context, string) ([]hermes.Model, error) {{
    return nil, nil
}}

func (f *workflowCITrustedHermes) ResponseExists(context.Context, string, string) (bool, error) {{
    return true, nil
}}

func (f *workflowCITrustedHermes) Capabilities(context.Context, string) (*hermes.Capabilities, error) {{
    return &hermes.Capabilities{{Features: map[string]any{{}}}}, nil
}}

type workflowCITrustedTranscriber struct{{}}

func (*workflowCITrustedTranscriber) Transcribe(context.Context, io.Reader, string) (string, error) {{
    return "", nil
}}

func workflowCITrustedCompletedWith(id, text string) []hermes.Event {{
    return []hermes.Event{{
        {{Type: hermes.EventTextDelta, Delta: text}},
        {{Type: hermes.EventTextDone, Text: text}},
        {{Type: hermes.EventCompleted, Response: &hermes.Response{{ID: id, Usage: &hermes.Usage{{InputTokens: 10}}}}}},
    }}
}}

func workflowCITrustedAPI(t *testing.T, h hermes.ChatServing) (*API, *gin.Engine) {{
    t.Helper()
    st, err := store.New(filepath.Join(t.TempDir(), "hidden.db"))
    if err != nil {{
        t.Fatalf("open hidden store: %v", err)
    }}
    t.Cleanup(func() {{ _ = st.Close() }})

    api := &API{{
        Store:       st,
        Hermes:      h,
        Transcriber: &workflowCITrustedTranscriber{{}},
        Profiles:    []string{{"default", "devops"}},
        AuthMode:    "proxy",
    }}

    gin.SetMode(gin.TestMode)
    router := gin.New()
    api.RegisterRoutes(router, func(c *gin.Context) {{ c.Next() }})
    return api, router
}}

func workflowCITrustedDo(
    t *testing.T,
    router *gin.Engine,
    method, path string,
    body any,
) *httptest.ResponseRecorder {{
    t.Helper()
    var reader io.Reader
    if body != nil {{
        raw, err := json.Marshal(body)
        if err != nil {{
            t.Fatalf("marshal hidden request: %v", err)
        }}
        reader = bytes.NewReader(raw)
    }}
    req := httptest.NewRequestWithContext(context.Background(), method, path, reader)
    if body != nil {{
        req.Header.Set("Content-Type", "application/json")
    }}
    rec := httptest.NewRecorder()
    router.ServeHTTP(rec, req)
    return rec
}}

func workflowCITrustedCreateThread(
    t *testing.T,
    router *gin.Engine,
    title, profile string,
) store.Thread {{
    t.Helper()
    rec := workflowCITrustedDo(
        t,
        router,
        http.MethodPost,
        "/api/threads",
        gin.H{{"title": title, "profile": profile}},
    )
    if rec.Code != http.StatusCreated {{
        t.Fatalf("create thread status=%d body=%s", rec.Code, rec.Body.String())
    }}
    var thread store.Thread
    if err := json.Unmarshal(rec.Body.Bytes(), &thread); err != nil {{
        t.Fatalf("decode thread: %v", err)
    }}
    return thread
}}

func TestWorkflowCIHiddenTurnFeedReplay(t *testing.T) {{
    f := newTurnFeed()
    for i := 1; i <= {frame_count}; i++ {{
        id := f.ajouter("{event}", gin.H{{"n": i}})
        if id != i {{ t.Fatalf("non-monotone id: got=%d want=%d", id, i) }}
    }}
    suite, clos, tooFar, _ := f.depuis({cursor})
    if clos || tooFar {{ t.Fatalf("unexpected state clos=%v tooFar=%v", clos, tooFar) }}
    if len(suite) != {frame_count - cursor} {{
        t.Fatalf("replay length=%d want={frame_count - cursor}", len(suite))
    }}
    for i, fr := range suite {{
        want := {cursor} + i + 1
        if fr.ID != want || fr.Event != "{event}" {{
            t.Fatalf("bad replay frame index=%d id=%d event=%s", i, fr.ID, fr.Event)
        }}
    }}
    f.clore()
    f.clore()
    _, clos, tooFar, _ = f.depuis({frame_count})
    if !clos || tooFar {{ t.Fatalf("close must be idempotent clos=%v tooFar=%v", clos, tooFar) }}
}}

func TestWorkflowCIHiddenApprovalChoicesFailClosed(t *testing.T) {{
    for _, choice := range []string{{"once", "session", "always", "deny"}} {{
        if !validApprovalChoice(choice) {{ t.Fatalf("valid choice rejected: %s", choice) }}
    }}
    for _, choice := range []string{{"", "{invalid_choice}", "allow", "yes", "ONCE"}} {{
        if validApprovalChoice(choice) {{ t.Fatalf("invalid choice accepted: %q", choice) }}
    }}
}}

func TestWorkflowCIHiddenReasoningClamp(t *testing.T) {{
    fake := &workflowCITrustedHermes{{events: workflowCITrustedCompletedWith("hidden-response", "ok")}}
    _, router := workflowCITrustedAPI(t, fake)
    thread := workflowCITrustedCreateThread(t, router, "Hidden", "")
    response := workflowCITrustedDo(t, router, http.MethodPost, "/api/threads/"+thread.ID+"/chat", gin.H{{
        "message": "{message}",
        "reasoning_effort": "xhigh",
    }})
    if response.Code != http.StatusOK {{
        t.Fatalf("xhigh request status=%d body=%s", response.Code, response.Body.String())
    }}
    if fake.lastReq.ReasoningEffort != "medium" {{
        t.Fatalf("xhigh reasoning=%q want=medium", fake.lastReq.ReasoningEffort)
    }}

    fake.events = workflowCITrustedCompletedWith("hidden-response-2", "ok2")
    thread2 := workflowCITrustedCreateThread(t, router, "Hidden2", "")
    response = workflowCITrustedDo(t, router, http.MethodPost, "/api/threads/"+thread2.ID+"/chat", gin.H{{
        "message": "{message}-default",
    }})
    if response.Code != http.StatusOK {{
        t.Fatalf("default request status=%d body=%s", response.Code, response.Body.String())
    }}
    if fake.lastReq.ReasoningEffort != "low" {{
        t.Fatalf("default reasoning=%q want=low", fake.lastReq.ReasoningEffort)
    }}
}}

func TestWorkflowCIHiddenRetryIsExactlyOnce(t *testing.T) {{
    fake := &workflowCITrustedHermes{{events: workflowCITrustedCompletedWith("hidden-retry", "done")}}
    api, router := workflowCITrustedAPI(t, fake)
    thread := workflowCITrustedCreateThread(t, router, "Retry", "")
    target, err := api.Store.AppendMessage(context.Background(), thread.ID, store.RoleUser, "{message}", "")
    if err != nil {{
        t.Fatalf("append retry target: %v", err)
    }}

    response := workflowCITrustedDo(t, router, http.MethodPost, "/api/threads/"+thread.ID+"/chat", gin.H{{
        "message": "{message}",
        "retry_message_id": target.ID,
    }})
    if response.Code != http.StatusOK {{
        t.Fatalf("retry status=%d body=%s", response.Code, response.Body.String())
    }}

    messages, err := api.Store.ListMessages(context.Background(), thread.ID)
    if err != nil {{
        t.Fatalf("list retry messages: %v", err)
    }}
    occurrences := 0
    for _, item := range messages {{
        if item.Role == store.RoleUser && item.Content == "{message}" {{ occurrences++ }}
    }}
    if occurrences != 1 {{
        t.Fatalf("retry duplicated user message: occurrences=%d messages=%s", occurrences, fmt.Sprint(messages))
    }}
}}
'''

    path = candidate / "pkg" / "handlers" / "workflow_ci_hidden_test.go"
    if not path.parent.is_dir():
        raise RuntimeError("candidate does not expose pkg/handlers")
    path.write_text(source, encoding="utf-8")
    try:
        completed = subprocess.run(
            [
                "go",
                "test",
                "./pkg/handlers",
                "-run",
                "^TestWorkflowCIHidden",
                "-count=1",
            ],
            cwd=candidate,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        path.unlink(missing_ok=True)

    if completed.returncode != 0:
        detail = (completed.stdout + "\n" + completed.stderr).strip()
        raise AssertionError(detail[-3000:])


def evaluate(candidate: Path, seed: int) -> list[dict[str, str]]:
    try:
        _run_hidden_go_test(candidate, seed)
    except AssertionError as exc:
        return [{"name": "conversation-runtime-invariants", "status": "fail", "detail": str(exc)}]
    return [{"name": "conversation-runtime-invariants", "status": "pass"}]
