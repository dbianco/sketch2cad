// Cases copied verbatim (inputs and expected points) from the API's hand-solved geometry tests,
// sketch2cad-api/tests/geometry/test_cases_by_hand.py, so that preview.ts is checked against the
// same numbers as the server's metrics.py. Keep them in sync when those tests change.
import type { Point2 } from "../store/types";
import type { Side, Solution } from "./preview";

export interface TriangulationFixture {
  /** Python test id (TRIANGULATION_CASES of test_triangulation_matches_hand_solution). */
  id: string;
  a: Point2;
  b: Point2;
  dA: number;
  dB: number;
  solution: Solution;
  hint: Point2;
  expected: Point2;
}

export interface BaselineFixture {
  /** Python test id (BASELINE_CASES of test_baseline_offset_matches_hand_solution). */
  id: string;
  a: Point2;
  b: Point2;
  s: number;
  d: number;
  side: Side;
  expected: Point2;
}

export const TRIANGULATION_FIXTURES: readonly TriangulationFixture[] = [
  // test_triangulation_matches_hand_solution[3-4-5 nearest]
  { id: "3-4-5 nearest", a: [0, 0], b: [5, 0], dA: 3, dB: 4, solution: "nearest", hint: [2, 2], expected: [1.8, 2.4] },
  // test_triangulation_matches_hand_solution[3-4-5 alt]
  { id: "3-4-5 alt", a: [0, 0], b: [5, 0], dA: 3, dB: 4, solution: "alt", hint: [2, 2], expected: [1.8, -2.4] },
  // test_triangulation_matches_hand_solution[vertical 3-4-5]
  { id: "vertical 3-4-5", a: [1, 1], b: [1, 6], dA: 3, dB: 4, solution: "nearest", hint: [3, 3], expected: [3.4, 2.8] },
];

export const BASELINE_FIXTURES: readonly BaselineFixture[] = [
  // test_baseline_offset_matches_hand_solution[road left]
  { id: "road left", a: [0, 0], b: [10, 0], s: 1.5, d: 0.8, side: "left", expected: [1.5, 0.8] },
  // test_baseline_offset_matches_hand_solution[oblique right]
  { id: "oblique right", a: [2, 1], b: [5, 5], s: 5, d: 2, side: "right", expected: [6.6, 3.8] },
];
