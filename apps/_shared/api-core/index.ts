/**
 * Public exports for ``apps/_shared/api-core``.
 *
 * Each frontend imports from here rather than reaching into a specific
 * module path; this keeps the package's surface stable while allowing
 * internal reorganisation.
 */

export {
  ApiError,
  asApiError,
  setSessionTrigger,
  clearSessionTrigger,
} from "./ApiError";
