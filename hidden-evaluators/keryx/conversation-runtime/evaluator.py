from __future__ import annotations

import random
from pathlib import Path

from sandbox import CandidateSandbox

GO_BRIDGE = r'''package handlers

import (
    "context"
    "encoding/json"
    "fmt"
    "net/http"
    "os"
    "testing"

    "github.com/gin-gonic/gin"

    "github.com/didlawowo/keryx/pkg/store"
)

type workflowCIBridgeRequest struct {
    Event         string `json:"event"`
    Cursor        int    `json:"cursor"`
    FrameCount    int    `json:"frame_count"`
    InvalidChoice string `json:"invalid_choice"`
    Message       string `json:"message"`
}

type workflowCIBridgeResult struct {
    IDs              []int           `json:"ids"`
    ReplayIDs        []int           `json:"replay_ids"`
    ReplayEvents     []string        `json:"replay_events"`
    Closed           bool            `json:"closed"`
    TooFar           bool            `json:"too_far"`
    ApprovalValidity map[string]bool `json:"approval_validity"`
    XHighReasoning   string          `json:"xhigh_reasoning"`
    DefaultReasoning string          `json:"default_reasoning"`
    RetryOccurrences int             `json:"retry_occurrences"`
}

func TestWorkflowCIBridge(t *testing.T) {
    var request workflowCIBridgeRequest
    if err := json.Unmarshal([]byte(os.Getenv("WORKFLOW_CI_REQUEST")), &request); err != nil {
        t.Fatalf("invalid bridge request: %v", err)
    }

    result := workflowCIBridgeResult{
        ApprovalValidity: map[string]bool{},
    }

    feed := newTurnFeed()
    for i := 1; i <= request.FrameCount; i++ {
        result.IDs = append(result.IDs, feed.ajouter(request.Event, gin.H{"n": i}))
    }
    replay, _, _, _ := feed.depuis(request.Cursor)
    for _, frame := range replay {
        result.ReplayIDs = append(result.ReplayIDs, frame.ID)
        result.ReplayEvents = append(result.ReplayEvents, frame.Event)
    }
    feed.clore()
    feed.clore()
    _, result.Closed, result.TooFar, _ = feed.depuis(request.FrameCount)

    for _, choice := range []string{"once", "session", "always", "deny", "", request.InvalidChoice, "allow", "yes", "ONCE"} {
        result.ApprovalValidity[choice] = validApprovalChoice(choice)
    }

    fake := &fakeHermes{events: completedWith("bridge-response", "ok")}
    _, router := newTestAPI(t, fake, &fakeTranscriber{})
    thread := createThread(t, router, "Bridge", "")
    response := do(t, router, http.MethodPost, "/api/threads/"+thread.ID+"/chat", gin.H{
        "message": request.Message,
        "reasoning_effort": "xhigh",
    })
    if response.Code != http.StatusOK {
        t.Fatalf("xhigh request status=%d", response.Code)
    }
    result.XHighReasoning = fake.lastReq.ReasoningEffort

    fake.events = completedWith("bridge-response-2", "ok2")
    thread2 := createThread(t, router, "Bridge2", "")
    response = do(t, router, http.MethodPost, "/api/threads/"+thread2.ID+"/chat", gin.H{
        "message": request.Message + "-default",
    })
    if response.Code != http.StatusOK {
        t.Fatalf("default request status=%d", response.Code)
    }
    result.DefaultReasoning = fake.lastReq.ReasoningEffort

    retryFake := &fakeHermes{events: completedWith("bridge-retry", "done")}
    api, retryRouter := newTestAPI(t, retryFake, &fakeTranscriber{})
    retryThread := createThread(t, retryRouter, "Retry", "")
    target, err := api.Store.AppendMessage(
        context.Background(),
        retryThread.ID,
        store.RoleUser,
        request.Message,
        "",
    )
    if err != nil {
        t.Fatalf("append retry target: %v", err)
    }
    response = do(t, retryRouter, http.MethodPost, "/api/threads/"+retryThread.ID+"/chat", gin.H{
        "message": request.Message,
        "retry_message_id": target.ID,
    })
    if response.Code != http.StatusOK {
        t.Fatalf("retry request status=%d", response.Code)
    }
    messages, err := api.Store.ListMessages(context.Background(), retryThread.ID)
    if err != nil {
        t.Fatalf("list retry messages: %v", err)
    }
    for _, item := range messages {
        if item.Role == store.RoleUser && item.Content == request.Message {
            result.RetryOccurrences++
        }
    }

    payload, err := json.Marshal(result)
    if err != nil {
        t.Fatalf("marshal bridge result: %v", err)
    }
    fmt.Println("__WORKFLOW_CI_RESULT__=" + string(payload))
}
'''


def _run_hidden_observations(candidate: Path, seed: int) -> None:
    rng = random.Random(seed)
    request = {
        "message": f"hidden-{rng.getrandbits(64):016x}",
        "event": f"evt-{rng.getrandbits(40):010x}",
        "invalid_choice": f"invalid-{rng.getrandbits(32):08x}",
        "cursor": rng.randint(1, 5),
    }
    request["frame_count"] = rng.randint(request["cursor"] + 3, request["cursor"] + 12)

    with CandidateSandbox(candidate) as sandbox:
        sandbox.prepare_go()
        observed = sandbox.request_go_test(GO_BRIDGE, request)

    frame_count = request["frame_count"]
    cursor = request["cursor"]
    expected_ids = list(range(1, frame_count + 1))
    if observed.get("ids") != expected_ids:
        raise AssertionError(
            f"non-monotone feed ids: got={observed.get('ids')} want={expected_ids}"
        )

    expected_replay = list(range(cursor + 1, frame_count + 1))
    if observed.get("replay_ids") != expected_replay:
        raise AssertionError(
            "replay did not return every later frame exactly once: "
            f"got={observed.get('replay_ids')} want={expected_replay}"
        )
    if observed.get("replay_events") != [request["event"]] * len(expected_replay):
        raise AssertionError("replay attached a frame to the wrong event")
    if observed.get("closed") is not True or observed.get("too_far") is not False:
        raise AssertionError("feed close is not idempotent/fail-safe")

    validity = observed.get("approval_validity")
    if not isinstance(validity, dict):
        raise AssertionError("approval bridge returned no validity map")
    for choice in ("once", "session", "always", "deny"):
        if validity.get(choice) is not True:
            raise AssertionError(f"valid approval rejected: {choice}")
    for choice in ("", request["invalid_choice"], "allow", "yes", "ONCE"):
        if validity.get(choice) is not False:
            raise AssertionError(f"invalid approval accepted: {choice!r}")

    if observed.get("xhigh_reasoning") != "medium":
        raise AssertionError("xhigh reasoning was not clamped to medium")
    if observed.get("default_reasoning") != "low":
        raise AssertionError("omitted reasoning did not default to low")
    if observed.get("retry_occurrences") != 1:
        raise AssertionError(
            "retry duplicated persisted user message: "
            f"occurrences={observed.get('retry_occurrences')}"
        )


def evaluate(candidate: Path, seed: int) -> list[dict[str, str]]:
    try:
        _run_hidden_observations(candidate, seed)
    except AssertionError as exc:
        return [
            {
                "name": "conversation-runtime-invariants",
                "status": "fail",
                "detail": str(exc),
            }
        ]
    return [{"name": "conversation-runtime-invariants", "status": "pass"}]
