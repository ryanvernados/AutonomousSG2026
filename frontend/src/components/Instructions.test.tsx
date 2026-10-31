import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import Instructions from "./Instructions";
import { MockClient } from "../api/mock";
import { initialSnapshot } from "../api/fixtures";
it("requires an explicit preview and confirmation before issuing a restriction", async () => {
  const user = userEvent.setup(),
    confirm = vi.fn().mockResolvedValue(undefined);
  render(
    <Instructions
      snapshot={initialSnapshot()}
      client={new MockClient()}
      sessionId="demo"
      onConfirm={confirm}
      onClose={() => {}}
    />,
  );
  await user.click(screen.getByRole("button", { name: /Preview instruction/ }));
  expect(confirm).not.toHaveBeenCalled();
  expect(screen.getByText("READ-ONLY PREVIEW")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Confirm instruction" }));
  expect(confirm).toHaveBeenCalledOnce();
  expect(confirm.mock.calls[0][0]).toMatchObject({
    kind: "RUNWAY_CLOSURE",
    resource_id: "28R",
  });
});
it("requires a fresh preview after editing a draft", async () => {
  const user = userEvent.setup();
  render(
    <Instructions
      snapshot={initialSnapshot()}
      client={new MockClient()}
      sessionId="demo"
      onConfirm={vi.fn()}
      onClose={() => {}}
    />,
  );
  await user.click(screen.getByRole("button", { name: /Preview instruction/ }));
  await user.selectOptions(screen.getByLabelText("Affected resource"), "28L");
  expect(
    screen.queryByRole("button", { name: "Confirm instruction" }),
  ).not.toBeInTheDocument();
});
