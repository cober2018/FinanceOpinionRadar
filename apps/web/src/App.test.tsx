import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";

describe("App", () => {
  it("renders product shell with nav", async () => {
    render(<App />);
    expect(screen.getByText("财经观点雷达")).toBeTruthy();
    for (const t of ["总览", "主播", "视频库", "设置"]) {
      expect(screen.getByRole("button", { name: t })).toBeTruthy();
    }
  });
});
