// How numbers and dates read on screen (DESIGN.md, Numbers): thousands
// separators (1,872), a percent sign with no space (−4.8%), and dates as
// "Nov 20, 2025". Display only: the figures' `display` strings and the
// written explanations stay byte-for-byte what the server computed and
// validated (they are part of the replay key), so the tidying happens here,
// at render, and never feeds back into matching or arithmetic.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "2025-11-20" as "Nov 20, 2025"; anything else unchanged. */
export function formatIsoDate(text: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text.trim())
  if (match === null) return text
  const month = Number(match[2])
  const day = Number(match[3])
  if (month < 1 || month > 12 || day < 1 || day > 31) return text
  return `${MONTHS[month - 1]} ${day}, ${match[1]}`
}

/** A four-digit number from 1900 to 2099 standing alone is read as a year. */
function looksLikeYear(digits: string): boolean {
  return digits.length === 4 && /^(19|20)\d\d$/.test(digits)
}

/**
 * A figure or a sentence as people read it: ISO dates spelled out, whole
 * numbers of four or more digits grouped (never a year, a decimal's
 * fraction, or a number already grouped), and no space before a percent
 * sign.
 */
export function tidyNumbers(text: string): string {
  return text
    .replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (whole) => formatIsoDate(whole))
    .replace(/(^|[^\d.,])(\d{4,})(?![\d,]|\.\d)/g, (whole, before: string, digits: string) =>
      looksLikeYear(digits) ? whole : before + Number(digits).toLocaleString('en-US'),
    )
    .replace(/(\d)[\s\u00a0\u2009\u202f]+%/g, '$1%')
}
