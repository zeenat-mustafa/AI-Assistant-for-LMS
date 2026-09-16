/**

 * Phase 7.7 API client: lectures, student chat (SSE), and practice quizzes.

 * Everything mocks `fetch` -- no real backend is contacted.

 */



import { afterEach, describe, expect, it, vi } from "vitest";



import { ApiError } from "@/lib/api/client";

import { API_BASE_URL } from "@/lib/api/config";

import { downloadLecture, listLectures, uploadLecture } from "@/lib/api/lectures";

import {

  generateQuiz,

  generateQuizFromUpload,

  getQuizAttempt,

  getQuizHistory,

  submitQuiz,

} from "@/lib/api/quiz";

import { streamStudentChat } from "@/lib/api/student-chat";

import type { LectureFileRead, QuizGenerateRequest, StudentChatEvent } from "@/lib/api/types";



type FetchMock = ReturnType<typeof vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>>;



function mockJson(body: unknown, status = 200): FetchMock {

  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(

    async () =>

      new Response(JSON.stringify(body), {

        status,

        headers: { "content-type": "application/json" },

      }),

  );

  vi.stubGlobal("fetch", fetchMock);

  return fetchMock;

}



function sseResponse(chunks: string[]): FetchMock {

  const encoder = new TextEncoder();

  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(

    async () =>

      new Response(

        new ReadableStream<Uint8Array>({

          start(controller) {

            for (const c of chunks) controller.enqueue(encoder.encode(c));

            controller.close();

          },

        }),

        { status: 200, headers: { "content-type": "text/event-stream" } },

      ),

  );

  vi.stubGlobal("fetch", fetchMock);

  return fetchMock;

}



const frame = (event: object) => `data: ${JSON.stringify(event)}\n\n`;



function headersOf(fetchMock: FetchMock): Record<string, string> {

  return (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;

}



async function rejection(promise: Promise<unknown>): Promise<ApiError> {

  const error = await promise.then(() => null, (e: unknown) => e);

  expect(error).toBeInstanceOf(ApiError);

  return error as ApiError;

}



const LECTURE: LectureFileRead = {

  id: 3,

  session_id: 5,

  instructor_id: 1,

  original_filename: "Week10_Lecture.pptx",

  content_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",

  extracted: true,

  extraction_error: null,

  uploaded_at: "2026-09-12T10:00:00",

};



afterEach(() => {

  vi.unstubAllGlobals();

  vi.restoreAllMocks();

});



describe("lectures", () => {

  it("uploadLecture posts ONE file as FormData field `file`, with auth", async () => {

    const fetchMock = mockJson(LECTURE, 201);

    const file = new File(["PK"], "Week10_Lecture.pptx");

    const created = await uploadLecture(5, file, { token: "t" });



    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/sessions/5/lectures`);

    const init = fetchMock.mock.calls[0][1] as RequestInit;

    expect(init.method).toBe("POST");

    expect(init.body).toBeInstanceOf(FormData);

    expect((init.body as FormData).getAll("file")).toHaveLength(1);

    expect(((init.body as FormData).get("file") as File).name).toBe("Week10_Lecture.pptx");

    expect(headersOf(fetchMock)["Content-Type"]).toBeUndefined();

    expect(headersOf(fetchMock)["Authorization"]).toBe("Bearer t");

    expect(created).toEqual(LECTURE);

  });



  it("uploadLecture surfaces a 409 and a 422 with the backend's own detail", async () => {

    mockJson({ detail: "A lecture file named 'a.pptx' already exists in session 5. Use a different name." }, 409);

    const conflict = await rejection(uploadLecture(5, new File(["x"], "a.pptx"), { token: "t" }));

    expect(conflict.status).toBe(409);

    expect(conflict.detail).toBe(

      "A lecture file named 'a.pptx' already exists in session 5. Use a different name.",

    );



    mockJson({ detail: "Legacy .ppt format is not supported — please save as .pptx and re-upload." }, 422);

    const invalid = await rejection(uploadLecture(5, new File(["x"], "a.ppt"), { token: "t" }));

    expect(invalid.status).toBe(422);

    expect(invalid.detail).toBe(

      "Legacy .ppt format is not supported — please save as .pptx and re-upload.",

    );

  });



  it("listLectures GETs the session's lectures and preserves the backend order", async () => {

    const fetchMock = mockJson([{ ...LECTURE, id: 9 }, { ...LECTURE, id: 2 }]);

    const rows = await listLectures(5, { token: "t" });

    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/sessions/5/lectures`);

    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("GET");

    expect(rows.map((r) => r.id)).toEqual([9, 2]);

  });



  it("downloadLecture returns a Blob from the authenticated download route", async () => {

    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(

      async () => new Response(new Uint8Array([80, 75, 3, 4]), { status: 200 }),

    );

    vi.stubGlobal("fetch", fetchMock);

    const blob = await downloadLecture(5, 3, { token: "t" });

    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/sessions/5/lectures/3/download`);

    expect(headersOf(fetchMock)["Authorization"]).toBe("Bearer t");

    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(new Uint8Array([80, 75, 3, 4]));

  });



  it("downloadLecture surfaces a 404 detail", async () => {

    mockJson({ detail: "Lecture file 3 not found in session 5." }, 404);

    const error = await rejection(downloadLecture(5, 3, { token: "t" }));

    expect(error.detail).toBe("Lecture file 3 not found in session 5.");

  });

});



describe("quiz", () => {

  const payloads: QuizGenerateRequest[] = [

    { scope_type: "assignment_file", unsolved_file_id: 20 },

    { scope_type: "session", session_id: 7 },

    { scope_type: "multiple_sessions", session_ids: [5, 7] },

    { scope_type: "topic", topic_text: "tool calling" },

  ];



  it.each(payloads)("generateQuiz sends $scope_type as JSON with exactly one scope field", async (payload) => {

    const fetchMock = mockJson({}, 201);

    await generateQuiz(payload, { token: "t" });

    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/quiz/generate`);

    const init = fetchMock.mock.calls[0][1] as RequestInit;

    expect(init.method).toBe("POST");

    expect(headersOf(fetchMock)["Content-Type"]).toBe("application/json");

    expect(headersOf(fetchMock)["Authorization"]).toBe("Bearer t");

    expect(JSON.parse(init.body as string)).toEqual(payload);

  });



  it("the payload union makes an invalid scope combination unrepresentable (checked by tsc)", () => {

    const invalid = [

      // @ts-expect-error -- a session scope cannot also carry topic_text

      { scope_type: "session", session_id: 1, topic_text: "x" } satisfies QuizGenerateRequest,

      // @ts-expect-error -- a topic scope requires topic_text

      { scope_type: "topic" } satisfies QuizGenerateRequest,

      // @ts-expect-error -- uploaded_file is not a JSON scope

      { scope_type: "uploaded_file" } satisfies QuizGenerateRequest,

    ];

    expect(invalid).toHaveLength(3);

  });



  it("generateQuizFromUpload sends FormData field `file`, not JSON", async () => {

    const fetchMock = mockJson({}, 201);

    await generateQuizFromUpload(new File(["{}"], "lab.ipynb"), { token: "t" });

    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/quiz/generate/upload`);

    const init = fetchMock.mock.calls[0][1] as RequestInit;

    expect(init.body).toBeInstanceOf(FormData);

    expect(((init.body as FormData).get("file") as File).name).toBe("lab.ipynb");

    expect(headersOf(fetchMock)["Content-Type"]).toBeUndefined();

  });



  it("submitQuiz posts {answers}", async () => {

    const fetchMock = mockJson({}, 200);

    await submitQuiz(12, [0, 1, 2, 3, 0], { token: "t" });

    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/quiz/12/submit`);

    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toEqual({

      answers: [0, 1, 2, 3, 0],

    });

  });



  it("submitQuiz surfaces the 409 detail", async () => {

    mockJson({ detail: "This quiz attempt was already submitted and can't be re-scored." }, 409);

    const error = await rejection(submitQuiz(12, [0, 0, 0, 0, 0], { token: "t" }));

    expect(error.status).toBe(409);

    expect(error.detail).toBe("This quiz attempt was already submitted and can't be re-scored.");

  });



  it("getQuizAttempt and getQuizHistory GET their routes", async () => {

    const fetchMock = mockJson({ attempts: [], not_a_real_grade: true, notice: "n" });

    await getQuizAttempt(12, { token: "t" });

    await getQuizHistory({ token: "t" });

    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/quiz/12`);

    expect(fetchMock.mock.calls[1][0]).toBe(`${API_BASE_URL}/quiz/history`);

  });

});



describe("streamStudentChat", () => {

  const ALL_EVENTS: StudentChatEvent[] = [

    { event: "resolved", session_id: 7, session_title: "Week 10 Day 3", resolution: "current_session" },

    {

      event: "citations",

      citations: [

        {

          source_type: "lecture",

          source_file_id: 3,

          session_id: 7,

          similarity: 0.61,

          snippet: "Tool calling lets a model...",

          filename: "Week10_Lecture.pptx",

          slide_number: 4,

          source: "slide_text",

        },

      ],

    },

    { event: "token", text: "Tool " },

    { event: "token", text: "calling..." },

    { event: "done", thread_id: 1, user_message_id: 10, assistant_message_id: 11 },

  ];



  function recordingHandlers() {

    const seen: string[] = [];

    const record = (e: StudentChatEvent) => seen.push(e.event);

    return {

      seen,

      handlers: {

        onClarification: record,

        onResolved: record,

        onCitations: record,

        onToken: record,

        onDone: record,

        onError: record,

      },

    };

  }



  it("POSTs the question verbatim with current_session_id and the bearer token", async () => {

    const fetchMock = sseResponse([frame(ALL_EVENTS[4])]);

    await streamStudentChat({ question: "  what is bind_tools?\n", currentSessionId: 7 }, {}, { token: "t" });

    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/student-chat/stream`);

    const init = fetchMock.mock.calls[0][1] as RequestInit;

    expect(init.method).toBe("POST");

    expect(JSON.parse(init.body as string)).toEqual({

      question: "  what is bind_tools?\n",

      current_session_id: 7,

    });

    expect(headersOf(fetchMock)["Authorization"]).toBe("Bearer t");

  });



  it("dispatches resolved/citations/token/done in order and reports done", async () => {

    sseResponse(ALL_EVENTS.map(frame));

    const { seen, handlers } = recordingHandlers();

    const result = await streamStudentChat({ question: "q", currentSessionId: 7 }, handlers, { token: "t" });

    expect(seen).toEqual(["resolved", "citations", "token", "token", "done"]);

    expect(result).toEqual({ outcome: "done", unknownEventCount: 0 });

  });



  it("dispatches clarification_needed and reports clarification", async () => {

    sseResponse([

      frame({

        event: "clarification_needed",

        message: "I found a few sessions that could match — did you mean one of these? A, B",

        candidates: [

          { session_id: 5, session_title: "A", best_similarity: 0.58 },

          { session_id: 6, session_title: "B", best_similarity: 0.57 },

        ],

      }),

    ]);

    const onClarification = vi.fn();

    const result = await streamStudentChat({ question: "q", currentSessionId: null }, { onClarification }, { token: "t" });

    expect(onClarification).toHaveBeenCalledTimes(1);

    expect(onClarification.mock.calls[0][0].candidates).toHaveLength(2);

    expect(result.outcome).toBe("clarification");

  });



  it("dispatches error and reports error", async () => {

    sseResponse([

      frame(ALL_EVENTS[0]),

      frame({ event: "error", message: "The course assistant is temporarily unavailable." }),

    ]);

    const onError = vi.fn();

    const result = await streamStudentChat({ question: "q", currentSessionId: 7 }, { onError }, { token: "t" });

    expect(onError).toHaveBeenCalledWith({

      event: "error",

      message: "The course assistant is temporarily unavailable.",

    });

    expect(result.outcome).toBe("error");

  });



  it("reassembles a frame split across two network chunks", async () => {

    const whole = frame({ event: "token", text: "split across chunks" }) + frame(ALL_EVENTS[4]);

    sseResponse([whole.slice(0, 17), whole.slice(17, 40), whole.slice(40)]);

    const onToken = vi.fn();

    const result = await streamStudentChat({ question: "q", currentSessionId: 7 }, { onToken }, { token: "t" });

    expect(onToken).toHaveBeenCalledWith({ event: "token", text: "split across chunks" });

    expect(result.outcome).toBe("done");

  });



  it("ignores an unknown event name without throwing, and counts it", async () => {

    sseResponse([frame({ event: "mystery", x: 1 }), frame(ALL_EVENTS[4])]);

    const { seen, handlers } = recordingHandlers();

    const result = await streamStudentChat({ question: "q", currentSessionId: 7 }, handlers, { token: "t" });

    expect(seen).toEqual(["done"]);

    expect(result).toEqual({ outcome: "done", unknownEventCount: 1 });

  });



  it("reports a stream that closes without done as incomplete", async () => {

    sseResponse([frame(ALL_EVENTS[0]), frame({ event: "token", text: "half an ans" })]);

    const result = await streamStudentChat({ question: "q", currentSessionId: 7 }, {}, { token: "t" });

    expect(result.outcome).toBe("incomplete");

  });



  it("throws ApiError with the backend detail on a non-2xx response", async () => {

    mockJson({ detail: "Student role required." }, 403);

    const error = await rejection(streamStudentChat({ question: "q", currentSessionId: 7 }, {}, { token: "t" }));

    expect(error.status).toBe(403);

    expect(error.detail).toBe("Student role required.");

  });



  it("names the student-chat route (not /chat/stream) on a malformed frame", async () => {

    sseResponse(["data: {not json\n\n"]);

    const error = await rejection(streamStudentChat({ question: "q", currentSessionId: 7 }, {}, { token: "t" }));

    expect(error.detail).toContain("/student-chat/stream");

  });

});

