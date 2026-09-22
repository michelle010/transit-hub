import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import Autocomplete from "./Autocomplete";

const baseProps = {
  id: "transfer-city",
  name: "transfer_city",
  label: "中转城市",
  value: "成",
  placeholder: "例如：成都",
  isLoading: false,
  error: null,
  isOpen: true,
  activeIndex: 0,
  onChange: vi.fn(),
  onOpen: vi.fn(),
  onClose: vi.fn(),
  onActiveIndexChange: vi.fn(),
  onSelect: vi.fn(),
};

describe("Autocomplete", () => {
  it("renders combobox, listbox and active options with canonical labels", () => {
    const markup = renderToStaticMarkup(
      <Autocomplete {...baseProps} options={[{ id: "chengdu", label: "成都", meta: "四川省" }]} />,
    );

    expect(markup).toContain('role="combobox"');
    expect(markup).toContain('aria-autocomplete="list"');
    expect(markup).toContain('aria-expanded="true"');
    expect(markup).toContain('role="listbox"');
    expect(markup).toContain('role="option"');
    expect(markup).toContain("成都");
    expect(markup).toContain("四川省");
  });

  it("keeps manual entry available for empty and error states", () => {
    const emptyMarkup = renderToStaticMarkup(
      <Autocomplete {...baseProps} options={[]} activeIndex={-1} />,
    );
    const errorMarkup = renderToStaticMarkup(
      <Autocomplete
        {...baseProps}
        options={[]}
        activeIndex={-1}
        error="暂时无法加载建议，仍可直接输入名称。"
      />,
    );

    expect(emptyMarkup).toContain("仍可直接输入名称");
    expect(errorMarkup).toContain("暂时无法加载建议");
    expect(errorMarkup).toContain('name="transfer_city"');
  });
});
