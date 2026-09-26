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
    "context"
    "fmt"
    "net/http"
    "testing"

    "github.com/gin-gonic/gin"
    "github.com/stretchr/testify/require"

    "github.com/didlawowo/keryx/pkg/store"
)

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
    fake := &fakeHermes{{events: completedWith("hidden-response", "ok")}}
    _, router := newTestAPI(t, fake, &fakeTranscriber{{}})
    thread := createThread(t, router, "Hidden", "")
    response := do(t, router, http.MethodPost, "/api/threads/"+thread.ID+"/chat", gin.H{{
        "message": "{message}",
        "reasoning_effort": "xhigh",
    }})
    require.Equal(t, http.StatusOK, response.Code)
    require.Equal(t, "medium", fake.lastReq.ReasoningEffort)

    fake.events = completedWith("hidden-response-2", "ok2")
    thread2 := createThread(t, router, "Hidden2", "")
    response = do(t, router, http.MethodPost, "/api/threads/"+thread2.ID+"/chat", gin.H{{
        "message": "{message}-default",
    }})
    require.Equal(t, http.StatusOK, response.Code)
    require.Equal(t, "low", fake.lastReq.ReasoningEffort)
}}

func TestWorkflowCIHiddenRetryIsExactlyOnce(t *testing.T) {{
    fake := &fakeHermes{{events: completedWith("hidden-retry", "done")}}
    api, router := newTestAPI(t, fake, &fakeTranscriber{{}})
    thread := createThread(t, router, "Retry", "")
    target, err := api.Store.AppendMessage(context.Background(), thread.ID, store.RoleUser, "{message}", "")
    require.NoError(t, err)

    response := do(t, router, http.MethodPost, "/api/threads/"+thread.ID+"/chat", gin.H{{
        "message": "{message}",
        "retry_message_id": target.ID,
    }})
    require.Equal(t, http.StatusOK, response.Code)

    messages, err := api.Store.ListMessages(context.Background(), thread.ID)
    require.NoError(t, err)
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
