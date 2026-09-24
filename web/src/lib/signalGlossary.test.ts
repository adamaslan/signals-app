import { describe, expect, it } from "vitest";
import {
  DETECTORS,
  FAMILY_ORDER,
  detectorsByFamily,
  isPairedLabel,
  signalLabelCount,
} from "./signalGlossary";

describe("signal glossary", () => {
  it("documents all 18 default detectors exactly once", () => {
    // Mirrors signals_app.detection.orchestrator.get_default_detectors().
    expect(DETECTORS).toHaveLength(18);
    expect(new Set(DETECTORS.map((d) => d.id)).size).toBe(18);
  });

  it("groups detectors into the five scoring families", () => {
    const counts = FAMILY_ORDER.map((f) => detectorsByFamily(f).length);
    expect(counts).toEqual([4, 7, 3, 3, 1]);
    expect(detectorsByFamily("all")).toHaveLength(18);
  });

  it("gives every detector at least one fired signal and a reading", () => {
    for (const d of DETECTORS) {
      expect(d.signals.length).toBeGreaterThan(0);
      expect(d.readIt.length).toBeGreaterThan(0);
    }
  });

  it("gives direction-less signals zero weight", () => {
    for (const s of DETECTORS.flatMap((d) => d.signals)) {
      if (s.side === "none") expect(s.weight).toBe(0);
      else expect(s.weight).toBeGreaterThan(0);
    }
  });

  it("counts paired labels as two signals", () => {
    expect(isPairedLabel("MACD BULL / BEAR CROSS")).toBe(true);
    expect(isPairedLabel("GOLDEN CROSS")).toBe(false);
    expect(signalLabelCount()).toBeGreaterThan(DETECTORS.length * 2);
  });
});
