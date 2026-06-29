import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

test("renders the dashboard with the default mock show", () => {
  render(<App />);

  expect(screen.getByRole("heading", { name: /live music demand dashboard/i })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: /demo show/i })).toBeInTheDocument();
  expect(screen.getByText(/turnover, narrow head, she's green/i)).toBeInTheDocument();
  expect(screen.getByText(/ticket price history/i)).toBeInTheDocument();
  expect(screen.getByText(/google trends local interest/i)).toBeInTheDocument();
  expect(screen.getByText(/youtube artist signal/i)).toBeInTheDocument();
  expect(screen.getByText(/^forecast$/i)).toBeInTheDocument();
});

test("selecting another show updates the selected show details", async () => {
  const user = userEvent.setup();
  render(<App />);

  await user.selectOptions(screen.getByRole("combobox", { name: /demo show/i }), "rZ7HnEZ1AfPJGN");

  expect(screen.getByRole("heading", { name: /bingo loco/i })).toBeInTheDocument();
  expect(screen.getAllByText(/san jose improv/i).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/\$73/).length).toBeGreaterThan(0);
});
