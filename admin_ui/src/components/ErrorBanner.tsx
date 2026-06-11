interface Props {
  message: string;
  onDismiss?: () => void;
}

export function ErrorBanner({ message, onDismiss }: Props) {
  if (!message) {
    return null;
  }
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      {onDismiss ? (
        <button type="button" className="link-button" onClick={onDismiss}>Dismiss</button>
      ) : null}
    </div>
  );
}
