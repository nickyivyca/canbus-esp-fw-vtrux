#include "provenance.h"

namespace prov {

const char* statusName(Status s) {
  switch (s) {
    case MEASURED: return "MEASURED";
    case DATASHEET: return "DATASHEET";
    case DEFERRED: return "DEFERRED";
    case RECALLED: return "RECALLED";
    case UNKNOWN: return "UNKNOWN";
    case ASSUMED: return "ASSUMED";
    case NOT_MODELLED: return "NOT MODELLED";
  }
  return "?";
}

}  // namespace prov
