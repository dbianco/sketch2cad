import { describe, expect, it } from "vitest";
import { DISTANCE_MESSAGE, parseDistance } from "./parseDistance";

describe("parseDistance", () => {
  it("accepts a decimal comma or point, blanks and a leading plus", () => {
    expect(parseDistance("4,20")).toBe(4.2);
    expect(parseDistance("4.20")).toBe(4.2);
    expect(parseDistance(" 3,5 ")).toBe(3.5);
    expect(parseDistance("12")).toBe(12);
    expect(parseDistance("+0,75")).toBe(0.75);
    expect(parseDistance(",5")).toBe(0.5);
    expect(parseDistance("3.")).toBe(3);
  });

  it.each([
    "0",
    "0,00",
    "-3",
    "-0,5",
    "",
    "   ",
    "abc",
    "4,2m",
    "1e3",
    "1.000,50",
    "1,2,3",
    "Infinity",
    "NaN",
    "0x10",
    "+",
    ",",
  ])("rejects %j", (text) => {
    expect(parseDistance(text)).toBeNull();
  });

  it("has the V-04 message of the PRD", () => {
    expect(DISTANCE_MESSAGE).toBe("Ingresa una distancia numérica mayor que cero, por ejemplo 3,50.");
  });
});
