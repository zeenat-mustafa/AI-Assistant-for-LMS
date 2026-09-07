/** Tests for the localStorage-backed token store, including SSR safety. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearToken, getToken, setToken } from "@/lib/api/token-storage";

/** Minimal in-memory localStorage stand-in (tests run in the node env). */
function fakeWindow() {
  const store = new Map<string, string>();
  return {
    localStorage: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    },
  };
}

beforeEach(() => {
  vi.stubGlobal("window", fakeWindow());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("token storage", () => {
  it("round-trips a token and clears it", () => {
    expect(getToken()).toBeNull();
    setToken("jwt-value");
    expect(getToken()).toBe("jwt-value");
    clearToken();
    expect(getToken()).toBeNull();
  });

  it("returns null and does not throw when there is no window (SSR)", () => {
    vi.stubGlobal("window", undefined);
    expect(getToken()).toBeNull();
    expect(() => setToken("x")).not.toThrow();
    expect(() => clearToken()).not.toThrow();
  });

  it("returns null when localStorage access throws (blocked storage)", () => {
    vi.stubGlobal("window", {
      localStorage: {
        getItem: () => {
          throw new Error("blocked");
        },
        setItem: () => {
          throw new Error("blocked");
        },
        removeItem: () => {
          throw new Error("blocked");
        },
      },
    });
    expect(getToken()).toBeNull();
    expect(() => setToken("x")).not.toThrow();
  });
});
