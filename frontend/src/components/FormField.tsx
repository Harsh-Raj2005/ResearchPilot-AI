import type { InputHTMLAttributes } from "react";

interface FormFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  id: string;
  error?: string;
}

/**
 * Encapsulates label -> input -> error association only. No internal
 * state, no validation, no submission handling — LoginPage and
 * SignupPage remain fully responsible for their own state and the
 * actual auth API calls, unchanged. `htmlFor`/`id` wire the label to
 * the input; when `error` is set, `aria-invalid` and
 * `aria-describedby` point the input at the error message so screen
 * readers announce it.
 */
export default function FormField({ label, id, error, ...inputProps }: FormFieldProps) {
  const errorId = error ? `${id}-error` : undefined;

  return (
    <div className="form-field">
      <label className="form-field__label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="form-field__input"
        aria-invalid={error ? true : undefined}
        aria-describedby={errorId}
        {...inputProps}
      />
      {error && (
        <p className="form-field__error" id={errorId} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
