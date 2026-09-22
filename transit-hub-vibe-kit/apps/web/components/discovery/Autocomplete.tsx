"use client";

import type { KeyboardEvent, MouseEvent } from "react";

import type { DiscoveryOption } from "@/lib/discovery/types";

export interface AutocompleteProps {
  id: string;
  name: string;
  label: string;
  value: string;
  placeholder: string;
  options: DiscoveryOption[];
  isLoading: boolean;
  error: string | null;
  isOpen: boolean;
  activeIndex: number;
  onChange: (value: string) => void;
  onOpen: () => void;
  onClose: () => void;
  onActiveIndexChange: (index: number) => void;
  onSelect: (option: DiscoveryOption) => void;
  emptyMessage?: string;
  hint?: string;
}

function optionId(id: string, index: number): string {
  return `${id}-option-${index}`;
}

function preventBlur(event: MouseEvent<HTMLButtonElement>): void {
  event.preventDefault();
}

export default function Autocomplete({
  id,
  name,
  label,
  value,
  placeholder,
  options,
  isLoading,
  error,
  isOpen,
  activeIndex,
  onChange,
  onOpen,
  onClose,
  onActiveIndexChange,
  onSelect,
  emptyMessage = "没有找到匹配建议，仍可直接输入名称。",
  hint,
}: AutocompleteProps) {
  const listboxId = `${id}-suggestions`;
  const hasSuggestions = isOpen && options.length > 0;
  const showMessage =
    isOpen && !isLoading && options.length === 0 && (Boolean(error) || value.trim().length > 0);

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!isOpen) onOpen();
      if (options.length > 0) {
        onActiveIndexChange(activeIndex < options.length - 1 ? activeIndex + 1 : 0);
      }
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (options.length > 0) {
        onActiveIndexChange(activeIndex <= 0 ? options.length - 1 : activeIndex - 1);
      }
      return;
    }
    if (event.key === "Enter" && isOpen && activeIndex >= 0 && options[activeIndex]) {
      event.preventDefault();
      onSelect(options[activeIndex]);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  return (
    <div className="field autocomplete-field">
      <label htmlFor={id}>{label}</label>
      <div className="autocomplete-control">
        <input
          id={id}
          name={name}
          value={value}
          placeholder={placeholder}
          autoComplete="off"
          role="combobox"
          aria-autocomplete="list"
          aria-controls={listboxId}
          aria-expanded={hasSuggestions || showMessage || isLoading}
          aria-activedescendant={activeIndex >= 0 ? optionId(id, activeIndex) : undefined}
          aria-busy={isLoading}
          onChange={(event) => onChange(event.currentTarget.value)}
          onFocus={onOpen}
          onKeyDown={handleKeyDown}
          onBlur={() => window.setTimeout(onClose, 120)}
        />
        {isLoading && <span className="autocomplete-loading" aria-hidden="true" />}
      </div>
      {hint && <p className="field-help">{hint}</p>}

      {(hasSuggestions || showMessage || isLoading) && (
        <div
          className="autocomplete-popup"
          id={listboxId}
          role="listbox"
          aria-label={`${label}建议`}
        >
          {hasSuggestions && (
            <ul className="autocomplete-options">
              {options.map((option, index) => (
                <li key={option.id} role="presentation">
                  <button
                    id={optionId(id, index)}
                    className="autocomplete-option"
                    type="button"
                    role="option"
                    aria-selected={activeIndex === index}
                    onMouseDown={preventBlur}
                    onClick={() => onSelect(option)}
                    onMouseEnter={() => onActiveIndexChange(index)}
                  >
                    <span>{option.label}</span>
                    {option.meta && <small>{option.meta}</small>}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {isLoading && (
            <p className="autocomplete-message" role="status">
              正在加载建议…
            </p>
          )}
          {!isLoading && error && (
            <p className="autocomplete-message autocomplete-message-error" role="status">
              {error}
            </p>
          )}
          {!isLoading && !error && options.length === 0 && value.trim().length > 0 && (
            <p className="autocomplete-message" role="status">
              {emptyMessage}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
