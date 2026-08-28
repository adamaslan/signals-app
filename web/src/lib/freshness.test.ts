import { describe, it, expect } from "vitest";
import { classifyFreshness } from "./freshness";

const NOW = Date.parse("2026-08-28T12:00:00Z");
const DAY = 24 * 60 * 60 * 1000;

describe("classifyFreshness", () => {
  it("< 1 day is fresh", () => {
    expect(classifyFreshness(NOW - 3 * 60 * 60 * 1000, NOW).level).toBe("fresh");
  });

  it("1–3 days is stale with the day count in the label", () => {
    const info = classifyFreshness(NOW - 3 * DAY, NOW);
    expect(info.level).toBe("stale");
    expect(info.ageDays).toBe(3);
    expect(info.label).toBe("Stale · 3d");
  });

  it("a 9-day-old bar is very-stale", () => {
    const info = classifyFreshness(NOW - 9 * DAY, NOW);
    expect(info.level).toBe("very-stale");
    expect(info.label).toBe("Very stale · 9d");
  });

  it("null / unparseable is unknown", () => {
    expect(classifyFreshness(null, NOW).level).toBe("unknown");
    expect(classifyFreshness("not-a-date", NOW).level).toBe("unknown");
  });

  it("accepts an ISO string", () => {
    expect(
      classifyFreshness("2026-08-28T09:00:00Z", NOW).level,
    ).toBe("fresh");
  });
});
