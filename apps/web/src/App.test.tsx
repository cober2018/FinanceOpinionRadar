import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";

describe("App", () => {
  it("renders monitor board with tabs", async () => {
    render(<App />);
    expect(screen.getByText("监控")).toBeTruthy();
    expect(screen.getByText("安全设置")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("主播")).toBeTruthy());
  });
});
