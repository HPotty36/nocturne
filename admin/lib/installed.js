// Whether the page runs as an installed app (the site's, whose scope holds admin/; there is no other). There a link
// with target="_blank" leaves the app (on an iPhone or iPad it opens Safari), so app.js opens the site in place instead.
// DOM-free: it is handed the window.

/** True in an installed app: display-mode standalone, or navigator.standalone on an iPhone or iPad home-screen app. */
export function installedApp(win) {
  return Boolean(win.matchMedia?.('(display-mode: standalone)').matches || win.navigator?.standalone === true);
}
