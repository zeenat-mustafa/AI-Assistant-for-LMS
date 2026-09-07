/**
 * Grading chat: prefill/verbatim send, PROGRESSIVE event rendering, all five
 * non-graded outcomes, cross-session handling, and roster-refresh triggering.
 * The SSE stream is mocked; no real network.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api";
import type { ChatStreamEvent } from "@/lib/api";

const streamChatMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, streamChat: (...a: unknown[]) => streamChatMock(...a) };
});

import { GradingChat, defaultInstruction, summaryNamesSession } from "./grading-chat";

const TITLE = "Week 3 Day 1";

/**
 * A stream whose events are released one at a time, so a test can assert on
 * what is on screen BETWEEN events. This is the point of the component: the
 * events must appear as they arrive, not all at once at the end.
 */
function controllableStream() {
  const gates: Array<() => void> = [];
  const queue: ChatStreamEvent[] = [];
  let finish: () => void = () => {};
  const finished = new Promise<void>((resolve) => {
    finish = resolve;
  });

  async function* generator() {
    for (let i = 0; ; i++) {
      await new Promise<void>((resolve) => gates.push(resolve));
      if (i >= queue.length) break;
      yield queue[i];
    }
  }

  return {
    generator,
    /** Queue an event and release it. */
    async emit(event: ChatStreamEvent) {
      queue.push(event);
      gates.shift()?.();
      await Promise.resolve();
      await Promise.resolve();
    },
    /** Let the generator run past the end, closing the stream. */
    async close() {
      gates.shift()?.();
      await Promise.resolve();
      finish();
      await finished;
    },
  };
}

/** Simple all-at-once stream for tests that don't care about timing. */
function streamOf(...events: ChatStreamEvent[]) {
  return (async function* () {
    for (const event of events) yield event;
  })();
}

async function send() {
  await userEvent.click(screen.getByRole("button", { name: /^send$/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("helpers", () => {
  it("prefills with this session's title", () => {
    expect(defaultInstruction("Week 3 Day 1")).toBe("grade Week 3 Day 1 ");
  });

  it("detects whether a summary message names this session", () => {
    // 3.5's wording: "Graded 1 of 1 submission in Week 3 Day 1."
    expect(summaryNamesSession("Graded 1 of 1 submission in Week 3 Day 1.", "Week 3 Day 1")).toBe(true);
    expect(summaryNamesSession("Graded 1 of 1 submission in Week 1 Day 1.", "Week 3 Day 1")).toBe(false);
    // Fails toward "ran elsewhere" when there is no message at all.
    expect(summaryNamesSession(undefined, "Week 3 Day 1")).toBe(false);
  });
});

describe("<GradingChat /> — sending", () => {
  it("prefills the input and sends it verbatim, without injecting anything", async () => {
    streamChatMock.mockReturnValue(streamOf());
    render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);

    const input = screen.getByLabelText("Instruction");
    expect(input).toHaveValue("grade Week 3 Day 1 ");

    await send();

    expect(streamChatMock).toHaveBeenCalledTimes(1);
    // Trimmed, but otherwise exactly what was in the box.
    expect(streamChatMock.mock.calls[0][0]).toBe("grade Week 3 Day 1");
  });

  it("sends an edited instruction unchanged, naming a different session", async () => {
    streamChatMock.mockReturnValue(streamOf());
    render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);

    const input = screen.getByLabelText("Instruction");
    await userEvent.clear(input);
    await userEvent.type(input, "grade Week 1 Day 1 for Fiza");
    await send();

    expect(streamChatMock.mock.calls[0][0]).toBe("grade Week 1 Day 1 for Fiza");
  });

  it("restores the prefill with 'Reset to this session'", async () => {
    render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);
    const input = screen.getByLabelText("Instruction");

    await userEvent.clear(input);
    await userEvent.type(input, "something else");
    await userEvent.click(screen.getByRole("button", { name: /reset to this session/i }));

    expect(input).toHaveValue("grade Week 3 Day 1 ");
  });

  it("does not send an empty instruction", async () => {
    render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);
    await userEvent.clear(screen.getByLabelText("Instruction"));
    await send();
    expect(streamChatMock).not.toHaveBeenCalled();
  });
});

describe("<GradingChat /> — progressive streaming", () => {
  it("renders each event AS IT ARRIVES, not batched at the end", async () => {
    const stream = controllableStream();
    streamChatMock.mockImplementation(() => stream.generator());
    const onGraded = vi.fn();

    render(<GradingChat sessionTitle={TITLE} onGraded={onGraded} />);
    await send();

    // Nothing yet — the stream is open but has yielded nothing.
    expect(screen.queryByText(/a\.ipynb/)).not.toBeInTheDocument();

    await stream.emit({ event: "checking", student_id: 6, filename: "a.ipynb" });
    await waitFor(() => expect(screen.getByText(/Checking/)).toBeInTheDocument());
    expect(screen.getByText(/a\.ipynb/)).toBeInTheDocument();
    // The later file has NOT appeared yet — proof this is not batched.
    expect(screen.queryByText(/b\.ipynb/)).not.toBeInTheDocument();
    // And no summary yet.
    expect(screen.queryByText(/graded ·/)).not.toBeInTheDocument();

    await stream.emit({ event: "graded", student_id: 6, filename: "a.ipynb", score: 8.5 });
    await waitFor(() => expect(screen.getByText(/8\.5 \/ 10/)).toBeInTheDocument());
    expect(screen.queryByText(/b\.ipynb/)).not.toBeInTheDocument();

    await stream.emit({ event: "checking", student_id: 7, filename: "b.ipynb" });
    await waitFor(() => expect(screen.getByText(/b\.ipynb/)).toBeInTheDocument());
    // The first file's result is still on screen — events accumulate.
    expect(screen.getByText(/8\.5 \/ 10/)).toBeInTheDocument();

    await stream.emit({
      event: "summary",
      total: 2,
      graded: 2,
      failed: 0,
      failures: [],
      message: `Graded 2 of 2 submissions in ${TITLE}.`,
    });
    await waitFor(() =>
      expect(screen.getByText(`Graded 2 of 2 submissions in ${TITLE}.`)).toBeInTheDocument(),
    );

    await stream.close();
    await waitFor(() => expect(onGraded).toHaveBeenCalledTimes(1));
  });

  it("shows failed files and the failure list without aborting the run", async () => {
    streamChatMock.mockReturnValue(
      streamOf(
        { event: "checking", student_id: 6, filename: "bad.ipynb" },
        { event: "failed", student_id: 6, filename: "bad.ipynb", error: "No rubric" },
        {
          event: "summary",
          total: 1,
          graded: 0,
          failed: 1,
          failures: [{ student_id: 6, filename: "bad.ipynb", error: "No rubric" }],
          message: `Graded 0 of 1 submission in ${TITLE}. 1 failed — see the details below.`,
        },
      ),
    );
    render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);
    await send();

    await waitFor(() => expect(screen.getByText(/Failed/)).toBeInTheDocument());
    expect(screen.getByText(/0 graded · 1 failed · 1 total/)).toBeInTheDocument();
    expect(screen.getByText(/bad\.ipynb: No rubric/)).toBeInTheDocument();
  });

  it("disables the input while a run streams", async () => {
    const stream = controllableStream();
    streamChatMock.mockImplementation(() => stream.generator());

    render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);
    await send();

    await waitFor(() => expect(screen.getByLabelText("Instruction")).toBeDisabled());
    expect(screen.getByRole("button", { name: /grading…/i })).toBeDisabled();

    await stream.close();
    await waitFor(() => expect(screen.getByLabelText("Instruction")).toBeEnabled());
  });
});

describe("<GradingChat /> — the five non-graded outcomes are replies, not errors", () => {
  it("shows no_session_match as a normal message", async () => {
    streamChatMock.mockReturnValue(
      streamOf({
        status: "no_session_match",
        message: "I couldn't find a session matching that instruction.",
      }),
    );
    render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);
    await send();

    await waitFor(() =>
      expect(screen.getByText(/couldn't find a session matching/i)).toBeInTheDocument(),
    );
    // Not an error state.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("lists ambiguous_session candidates with their confidence", async () => {
    streamChatMock.mockReturnValue(
      streamOf({
        status: "ambiguous_session",
        message: "I found a few sessions that could match — did you mean one of these?",
        candidates: [
          { session_id: 1, session_title: "Week 1 Day 1", confidence: 1 },
          { session_id: 2, session_title: "Week 1 Day 2", confidence: 0.82 },
        ],
      }),
    );
    render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);
    await send();

    await waitFor(() => expect(screen.getByText(/Week 1 Day 1/)).toBeInTheDocument());
    expect(screen.getByText(/100% match/)).toBeInTheDocument();
    expect(screen.getByText(/82% match/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("lists ambiguous_student candidates", async () => {
    streamChatMock.mockReturnValue(
      streamOf({
        status: "ambiguous_student",
        message: "More than one student could match that name — did you mean: Sam or Samir?",
        session_id: 5,
        session_title: TITLE,
        candidates: [
          { student_id: 6, student_name: "Sam" },
          { student_id: 7, student_name: "Samir" },
        ],
      }),
    );
    render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);
    await send();

    await waitFor(() => expect(screen.getByText("Sam")).toBeInTheDocument());
    expect(screen.getByText("Samir")).toBeInTheDocument();
  });

  it("shows student_not_found and unsupported_filter as messages", async () => {
    streamChatMock.mockReturnValueOnce(
      streamOf({
        status: "student_not_found",
        message: "I couldn't find a student matching 'Zed' in that session.",
        attempted_name: "Zed",
      }),
    );
    const { unmount } = render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);
    await send();
    await waitFor(() => expect(screen.getByText(/matching 'Zed'/)).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    unmount();

    streamChatMock.mockReturnValueOnce(
      streamOf({
        status: "unsupported_filter",
        message: "I can't handle that kind of filtering yet (exclusion).",
        reason: "exclusion",
      }),
    );
    render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);
    await send();
    await waitFor(() =>
      expect(screen.getByText(/can't handle that kind of filtering/i)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("never refreshes the roster for a non-graded outcome", async () => {
    const onGraded = vi.fn();
    streamChatMock.mockReturnValue(
      streamOf({ status: "no_session_match", message: "Nope." }),
    );
    render(<GradingChat sessionTitle={TITLE} onGraded={onGraded} />);
    await send();

    await waitFor(() => expect(screen.getByText("Nope.")).toBeInTheDocument());
    expect(onGraded).not.toHaveBeenCalled();
  });
});

describe("<GradingChat /> — cross-session and failures", () => {
  it("warns and skips the roster refresh when the run named another session", async () => {
    const onGraded = vi.fn();
    streamChatMock.mockReturnValue(
      streamOf({
        event: "summary",
        total: 1,
        graded: 1,
        failed: 0,
        failures: [],
        message: "Graded 1 of 1 submission in Week 1 Day 1.",
      }),
    );
    render(<GradingChat sessionTitle={TITLE} onGraded={onGraded} />);
    await send();

    await waitFor(() =>
      expect(screen.getByText(/resolved to a different session/i)).toBeInTheDocument(),
    );
    expect(onGraded).not.toHaveBeenCalled();
  });

  it("refreshes the roster when the run named this session", async () => {
    const onGraded = vi.fn();
    streamChatMock.mockReturnValue(
      streamOf({
        event: "summary",
        total: 1,
        graded: 1,
        failed: 0,
        failures: [],
        message: `Graded 1 of 1 submission in ${TITLE}.`,
      }),
    );
    render(<GradingChat sessionTitle={TITLE} onGraded={onGraded} />);
    await send();

    await waitFor(() => expect(onGraded).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/resolved to a different session/i)).not.toBeInTheDocument();
  });

  it("shows a transport failure as an actual error, unlike the outcomes", async () => {
    streamChatMock.mockImplementation(() => {
      throw new ApiError(403, "Instructor role required.");
    });
    render(<GradingChat sessionTitle={TITLE} onGraded={vi.fn()} />);
    await send();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Instructor role required."),
    );
  });
});
