import React, { useState } from "react";

import { setApiBaseUrl } from "../api/client";

interface Props {
  initialValue: string;
  onSave: (next: string) => void;
  onCancel: () => void;
}

export const SettingsPanel: React.FC<Props> = ({ initialValue, onSave, onCancel }) => {
  const [value, setValue] = useState(initialValue);

  const handleSave = () => {
    const trimmed = value.trim();
    if (trimmed.length === 0) return;
    setApiBaseUrl(trimmed);
    onSave(trimmed);
  };

  return (
    <div className="kw-settings" role="dialog" aria-label="Widget settings">
      <div className="kw-settings__label">KW-Pipeline API base URL</div>
      <input
        type="url"
        className="kw-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="https://kw-pipeline.example.com"
        autoFocus
      />
      <div className="kw-settings__hint">
        Persisted per tile. Leave at <code>http://localhost:8000</code> for the
        local <code>make demo-api</code> backend.
      </div>
      <div className="kw-settings__actions">
        <button type="button" className="kw-btn" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="kw-btn kw-btn--primary"
          onClick={handleSave}
          disabled={value.trim().length === 0}
        >
          Save
        </button>
      </div>
    </div>
  );
};
