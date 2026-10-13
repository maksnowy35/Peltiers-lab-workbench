/** Minimal typings for the parts of plotly.js used by teglab (avoids a large @types dependency). */
declare module 'plotly.js-dist-min' {
  export interface PlotlyHTMLElement extends HTMLElement {}
  export function react(
    root: HTMLElement,
    data: object[],
    layout?: object,
    config?: object,
  ): Promise<PlotlyHTMLElement>;
  export function purge(root: HTMLElement): void;
  export const Plots: { resize(root: HTMLElement): Promise<void> | void };
  const Plotly: {
    react: typeof react;
    purge: typeof purge;
    Plots: typeof Plots;
  };
  export default Plotly;
}
