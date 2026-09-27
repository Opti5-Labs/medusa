import type { SVGProps } from "react";

const paths = {
  home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z" /></>,
  github: <><path d="M9 19c-4 1-4-2-6-2m12 5v-4a3.5 3.5 0 0 0-1-2.7c3.3-.4 6.7-1.6 6.7-7A5.4 5.4 0 0 0 19.2 4a5 5 0 0 0-.1-3S17.9.6 15 2.5a14 14 0 0 0-6 0C6.1.6 4.9 1 4.9 1a5 5 0 0 0-.1 3A5.4 5.4 0 0 0 3.3 8.3c0 5.4 3.4 6.6 6.7 7A3.5 3.5 0 0 0 9 18v4" /></>,
  upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" /></>,
  history: <><path d="M3 11a9 9 0 1 1 2.5 7M3 4v7h7M12 7v5l3 2" /></>,
  layers: <><path d="m12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 16l10 5 10-5" /></>,
  spark: <><path d="M12 3c1 5 4 8 9 9-5 1-8 4-9 9-1-5-4-8-9-9 5-1 8-4 9-9Z" /></>,
  arrow: <><path d="M5 12h14m-5-5 5 5-5 5" /></>,
  chevron: <><path d="m9 5 7 7-7 7" /></>,
  search: <><circle cx="10.5" cy="10.5" r="7" /><path d="m16 16 5 5" /></>,
  shield: <><path d="m12 3 8 3v5c0 5-4 8.5-8 10-4-1.5-8-5-8-10V6Z" /><path d="m8.5 12 2.5 2.5 4.5-5" /></>,
  play: <><path d="m9 5 11 7-11 7Z" /></>,
  code: <><path d="m8 7-5 5 5 5m8-10 5 5-5 5M14 4l-4 16" /></>,
  settings: <><path d="m10 3-1 3-3 1-3 3 2 2-1 3 3 3 3-1 2 4 3-1 1-3 3-1 1-4-3-2V6l-4-1Z" /><circle cx="12" cy="12" r="3" /></>,
  // A circle half filled: light and dark, i.e. appearance.
  appearance: <><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5a8.5 8.5 0 0 1 0 17Z" fill="currentColor" stroke="none" /></>,
  motion: <><path d="M4 12h3m2-5h8m-8 10h8" /><circle cx="19" cy="7" r="1.6" /><circle cx="19" cy="17" r="1.6" /></>,
  contrast: <><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5v17M12 8h5.5M12 12h8.5M12 16h5.5" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-10v.2" /></>,
  close: <><path d="m6 6 12 12M6 18 18 6" /></>,
  check: <><path d="m5 12 4 4L19 6" /></>,
  cube: <><path d="m12 3 9 5v9l-9 5-9-5V8Zm0 10v9M3 8l9 5 9-5M7.5 5.5l9 5v5" /></>,
  folder: <><path d="M3 7V5a1 1 0 0 1 1-1h5l3 3h8a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z" /></>,
  moon: <><path d="M20 15.5A9 9 0 0 1 8.5 4 9 9 0 1 0 20 15.5Z" /></>,
  bolt: <><path d="m13 2-9 12h7l-1 8 10-13h-7Z" /></>,
  trash: <><path d="M4 7h16M9 7V4h6v3m-9 0 1 13h10l1-13" /></>,
  zoomIn: <><circle cx="10.5" cy="10.5" r="7" /><path d="M10.5 7.5v6M7.5 10.5h6" /><path d="m16 16 5 5" /></>,
  zoomOut: <><circle cx="10.5" cy="10.5" r="7" /><path d="M7.5 10.5h6" /><path d="m16 16 5 5" /></>,
  frame: <><path d="M4 9V5a1 1 0 0 1 1-1h4M20 9V5a1 1 0 0 0-1-1h-4M4 15v4a1 1 0 0 0 1 1h4M20 15v4a1 1 0 0 1-1 1h-4" /></>,
  // Line-art takes on the IBM Bob robot (hard hat and face) and the Granite nested cubes.
  bob: <><path d="M5.8 11a6.2 6.2 0 0 1 12.4 0M4.2 11.2h15.6M10.4 5.3v3.1M13.6 5.3v3.1" /><path d="M6.8 11.4v4.8a2.6 2.6 0 0 0 2.6 2.6h5.2a2.6 2.6 0 0 0 2.6-2.6v-4.8M5.1 13.2v2.6M18.9 13.2v2.6" /><circle cx="10" cy="14.3" r="1" fill="currentColor" stroke="none" /><circle cx="14" cy="14.3" r="1" fill="currentColor" stroke="none" /><path d="M10.5 16.5q1.5 1 3 0" /></>,
  granite: <><path d="M12 2.8 20.2 6.6v10.8L12 21.2l-8.2-3.8V6.6Z" /><path d="M3.8 6.6 12 10.4l8.2-3.8M12 10.4v10.8" /><path d="M14.6 7.6 18 9.2v4.5l-3.4 1.6-3.4-1.6V9.2Z" strokeOpacity="0.75" /><path d="M11.2 9.2l3.4 1.6L18 9.2M14.6 10.8v4.5" strokeOpacity="0.75" /></>,
};
export type IconName = keyof typeof paths;
export default function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}
