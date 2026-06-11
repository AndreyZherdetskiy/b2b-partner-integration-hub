import { useState } from "react";
import { getStoredToken, setStoredToken } from "../api/client";

export function TokenInput() {
  const [value, setValue] = useState(getStoredToken());

  function save() {
    setStoredToken(value);
  }

  return (
    <div className="token-input">
      <label htmlFor="admin-token">Admin token</label>
      <input
        id="admin-token"
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Bearer bootstrap token"
      />
      <button type="button" onClick={save}>Save session</button>
    </div>
  );
}
