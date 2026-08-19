import type { ButtonHTMLAttributes, ReactNode } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary";
  isLoading?: boolean;
  fullWidth?: boolean;
  children: ReactNode;
}

/**
 * Shared button used by LandingPage (CTA + secondary action),
 * LoginPage, and SignupPage (submit). Native <button> semantics are
 * preserved — type defaults to "button" so it never accidentally
 * submits a form unless explicitly given type="submit"; disabled and
 * aria-busy are wired for real keyboard/screen-reader behavior, not
 * just visual styling.
 */
export default function Button({
  variant = "primary",
  isLoading = false,
  fullWidth = false,
  disabled,
  children,
  className,
  type = "button",
  ...rest
}: ButtonProps) {
  const classes = [
    "button",
    variant === "primary" ? "button--primary" : "button--secondary",
    fullWidth ? "button--full-width" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      type={type}
      className={classes}
      disabled={disabled || isLoading}
      aria-busy={isLoading || undefined}
      {...rest}
    >
      {children}
    </button>
  );
}
