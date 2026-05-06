/**
 * Public surface of the transitional ``demo-toggle`` shared package.
 *
 * Both front-ends (``apps/explorer`` + ``apps/web``) import the
 * :func:`DemoToggle` component and the API helpers from here. Keeping
 * the surface narrow makes the rip-out trivial: ``git rm`` this folder
 * and delete the import + JSX call from each Settings modal.
 */

export { DemoToggle } from "./DemoToggle";
export {
  fetchDemoStatus,
  postDemoLoad,
  postDemoReset,
  DemoConflictError,
} from "./api";
export type {
  DemoLoadRequest,
  DemoStatusResponse,
  DemoConflictDetail,
} from "./api";
