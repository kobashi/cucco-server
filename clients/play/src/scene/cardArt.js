// Card face art: Roman numerals for the number ranks and original,
// flat-geometric pictogram SVGs for the named ranks. All icons are designed
// from scratch here (stroke-based primitives) -- the source decks' artwork
// is never referenced or imitated, per project policy. Inline SVG only:
// no external assets, and the markup stays cheap enough for flight-ghost
// cloning in the animation layer.

import { esc } from "../../../web-common/utils.js";

// Roman notation has no zero; rank "0" keeps a serif "0" so it reads
// instantly next to I..XII (the medieval "N" would just puzzle players).
// The Arabic value is always on the caption, so the mapping is on-card.
export const ROMAN = {
  0: "0", 1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI",
  7: "VII", 8: "VIII", 9: "IX", 10: "X", 11: "XI", 12: "XII",
};

const SVG_OPEN =
  '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" ' +
  'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">';

// One line each (original compositions):
//   道化 = three-triangle jester cap with bell dots on a shallow arc
//   獅子 = circle face ringed by short mane rays, dot eyes, triangle nose
//   仮面 = domino-mask band with almond eye cutouts and side ribbons
//   桶   = top-wide tub with two hoops and an arc handle
//   家   = square body + triangle roof + door + window dot
//   猫   = circle head + triangle ears + whisker strokes
//   馬   = arched-neck head profile with ear and mane strokes
//   人間 = pictogram bust (circle head over rounded torso)
//   クク = perched round bird (body/head circles, beak + tail triangles)
export const ICONS = {
  道化: `${SVG_OPEN}
    <path d="M5 16 Q12 13.4 19 16"/>
    <path d="M5 16 L4.2 7.6 L9.6 13.9"/>
    <path d="M9.8 14 L12 5 L14.2 14"/>
    <path d="M19 16 L19.8 7.6 L14.4 13.9"/>
    <circle cx="4.2" cy="7" r="1" fill="currentColor" stroke="none"/>
    <circle cx="12" cy="4.4" r="1" fill="currentColor" stroke="none"/>
    <circle cx="19.8" cy="7" r="1" fill="currentColor" stroke="none"/>
    <path d="M5.4 17.8 Q12 19.6 18.6 17.8"/>
  </svg>`,
  獅子: `${SVG_OPEN}
    <circle cx="12" cy="12.5" r="5"/>
    <path d="M12 5.9 V3.4 M12 19.1 V21.6 M18.6 12.5 H21.1 M5.4 12.5 H2.9
             M16.7 7.8 L18.5 6 M7.3 7.8 L5.5 6 M16.7 17.2 L18.5 19 M7.3 17.2 L5.5 19"/>
    <circle cx="10.2" cy="11.6" r="0.85" fill="currentColor" stroke="none"/>
    <circle cx="13.8" cy="11.6" r="0.85" fill="currentColor" stroke="none"/>
    <path d="M10.9 14.2 H13.1 L12 15.6 Z" fill="currentColor" stroke="none"/>
  </svg>`,
  仮面: `${SVG_OPEN}
    <path d="M3 9 C6.5 6.9 8.6 7.6 12 7.6 C15.4 7.6 17.5 6.9 21 9
             C20.5 13.4 17.8 15.9 15 14.8 C13.7 14.3 13 13.3 12 13.3
             C11 13.3 10.3 14.3 9 14.8 C6.2 15.9 3.5 13.4 3 9 Z"/>
    <path d="M6.2 10.3 C7.2 9.6 8.7 9.6 9.6 10.4 C8.7 11.3 7.1 11.3 6.2 10.3 Z" fill="currentColor" stroke="none"/>
    <path d="M17.8 10.3 C16.8 9.6 15.3 9.6 14.4 10.4 C15.3 11.3 16.9 11.3 17.8 10.3 Z" fill="currentColor" stroke="none"/>
    <path d="M3.1 9.4 L1.6 12.2 M20.9 9.4 L22.4 12.2"/>
  </svg>`,
  桶: `${SVG_OPEN}
    <path d="M4.5 8.5 H19.5 L17.8 20 H6.2 Z"/>
    <path d="M5.2 12.2 H18.8 M5.8 16.2 H18.2"/>
    <path d="M7.2 8.3 C7.2 4.4 16.8 4.4 16.8 8.3"/>
  </svg>`,
  家: `${SVG_OPEN}
    <path d="M3.5 11 L12 4 L20.5 11"/>
    <path d="M5.5 10.4 V20 H18.5 V10.4"/>
    <path d="M10.2 20 V14.6 H13.8 V20"/>
    <circle cx="16.2" cy="13.8" r="0.9" fill="currentColor" stroke="none"/>
  </svg>`,
  猫: `${SVG_OPEN}
    <circle cx="12" cy="13" r="6.2"/>
    <path d="M7.4 8.9 L6.2 3.6 L10.6 6.9"/>
    <path d="M16.6 8.9 L17.8 3.6 L13.4 6.9"/>
    <circle cx="9.8" cy="12" r="0.85" fill="currentColor" stroke="none"/>
    <circle cx="14.2" cy="12" r="0.85" fill="currentColor" stroke="none"/>
    <path d="M4.2 13 L7.4 13.6 M4.4 15.8 L7.5 15.2 M19.8 13 L16.6 13.6 M19.6 15.8 L16.5 15.2"/>
  </svg>`,
  馬: `${SVG_OPEN}
    <path d="M6 20.5 C6 13.2 8.6 8.2 14 6.1 L15.1 3.5 L17 6.4
             C18.7 7.5 19.6 9.4 19.2 11.1 L15.8 10.4
             C14.6 13.4 13.2 16.3 13.2 20.5"/>
    <circle cx="15.7" cy="7.7" r="0.8" fill="currentColor" stroke="none"/>
    <path d="M8.6 12.4 L10.8 13.3 M9.6 10 L11.7 11 M11.2 7.9 L13.1 9"/>
  </svg>`,
  人間: `${SVG_OPEN}
    <circle cx="12" cy="7" r="3.8"/>
    <path d="M5.5 21 C5.5 15.6 8.2 13.2 12 13.2 C15.8 13.2 18.5 15.6 18.5 21 Z"/>
  </svg>`,
  クク: `${SVG_OPEN}
    <path d="M3 19 H21"/>
    <circle cx="11" cy="13.6" r="4.7"/>
    <circle cx="16.4" cy="8.3" r="2.7"/>
    <path d="M18.9 7.9 L21.6 9 L19.1 10.2 Z" fill="currentColor" stroke="none"/>
    <circle cx="17" cy="7.7" r="0.65" fill="currentColor" stroke="none"/>
    <path d="M6.9 11.9 L2.9 9.6 L6.1 15.1"/>
  </svg>`,
};

// Inner markup for a face-up card: the art area + a small caption keeping
// the rank readable at a glance (Arabic value for numbers, name otherwise).
export function cardInnerHTML(rank) {
  if (Object.hasOwn(ROMAN, rank)) {
    return `<div class="card-art card-art--num">${ROMAN[rank]}</div><span class="card-caption">${esc(rank)}</span>`;
  }
  const icon = Object.hasOwn(ICONS, rank) ? ICONS[rank] : null;
  if (!icon) return `<span>${esc(rank)}</span>`; // unknown rank: old plain face
  const cucco = rank === "クク" ? " card-art--cucco" : "";
  return `<div class="card-art card-art--icon${cucco}">${icon}</div><span class="card-caption">${esc(rank)}</span>`;
}
