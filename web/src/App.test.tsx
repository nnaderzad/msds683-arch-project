import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

test("renders the dashboard with the default mock show", () => {
  render(<App />);

  expect(screen.getByRole("heading", { name: /live music demand dashboard/i })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: /demo show/i })).toBeInTheDocument();
  expect(screen.getByText(/turnover, narrow head, she's green/i)).toBeInTheDocument();
  expect(screen.getByText(/demand signals over time/i)).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: /observed price/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /forecast price/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /google trends/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /youtube views/i })).toBeChecked();
  expect(screen.getByText(/show date/i)).toBeInTheDocument();
  expect(screen.getByText(/right axis shows observed and forecasted price/i)).toBeInTheDocument();
  expect(screen.getByText(/auto-scales per selected show/i)).toBeInTheDocument();
  expect(screen.getAllByText(/forecasted price/i).length).toBeGreaterThan(0);
});

test("selecting another show updates the selected show details", async () => {
  const user = userEvent.setup();
  render(<App />);

  await user.selectOptions(screen.getByRole("combobox", { name: /demo show/i }), "rZ7HnEZ1AfPJGN");

  expect(screen.getByRole("heading", { name: /bingo loco/i })).toBeInTheDocument();
  expect(screen.getAllByText(/san jose improv/i).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/\$73/).length).toBeGreaterThan(0);
});
