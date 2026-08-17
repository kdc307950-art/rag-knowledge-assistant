import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "./client";
import { deleteDocument, uploadDocuments } from "./knowledge";

vi.mock("./client", () => ({
  request: vi.fn(),
}));

const requestMock = vi.mocked(request);

afterEach(() => {
  vi.clearAllMocks();
});

describe("knowledge upload API", () => {
  it("sends every selected file as a repeated multipart files field", async () => {
    requestMock.mockResolvedValue({ success: true });
    const first = new File(["alpha"], "a.txt", { type: "text/plain" });
    const second = new File(["beta"], "b.md", { type: "text/markdown" });

    await uploadDocuments([first, second]);

    expect(requestMock).toHaveBeenCalledOnce();
    const [path, init] = requestMock.mock.calls[0] ?? [];
    expect(path).toBe("/upload");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeInstanceOf(FormData);

    const body = init?.body as FormData;
    const entries = body.getAll("files") as File[];
    expect(entries).toHaveLength(2);
    expect(entries.map((file) => file.name)).toEqual(["a.txt", "b.md"]);
    expect(entries.map((file) => file.type)).toEqual(["text/plain", "text/markdown"]);
  });

  it("encodes document sources before issuing a delete request", async () => {
    requestMock.mockResolvedValue({ ok: true });
    const source = "资料 / #?.txt";

    await deleteDocument(source);

    expect(requestMock).toHaveBeenCalledWith(
      `/kb/documents/${encodeURIComponent(source)}`,
      { method: "DELETE" },
    );
  });
});
