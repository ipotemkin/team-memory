import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { validateDocument } from "../components/DocumentsTab";
import { reviewReason } from "../format";
import { parseHash } from "../router";

describe("утилиты", () => {
  it("parseHash", () => {
    expect(parseHash("#/documents/42")).toEqual({ tab: "documents", documentId: "42", runId: null });
    expect(parseHash("#/history/3")).toEqual({ tab: "history", documentId: null, runId: "3" });
    expect(parseHash("")).toEqual({ tab: "documents", documentId: null, runId: null });
  });
  it("лимиты документа", () => {
    expect(validateDocument("  ", "x").title).toBeDefined();
    expect(validateDocument("a".repeat(201), "x").title).toBeDefined();
    expect(validateDocument("a".repeat(200), "x".repeat(100000))).toEqual({});
    expect(validateDocument("a", "x".repeat(100001)).text).toBeDefined();
  });
  it("причины проверки по-русски", () => {
    for (const c of ["no_sources", "low_confidence", "unverified_quote", "llm_invalid_json", "llm_error"]) {
      expect(reviewReason(c)).toMatch(/[а-я]/);
    }
  });
  it("fieldErrors", () => {
    const e = new ApiError(422, "validation_error", [{ field: "body.text", msg: "too long" }], "x");
    expect(e.fieldErrors()).toEqual({ text: "too long" });
  });
});
