// How much weight a modelled behaviour can bear.
//
// Shared by every fake in this folder. It started inside `mcp2515_fake.h`
// and moved out when the TWAI fake needed the same vocabulary: two copies
// of a status scale drift, and a scale that means slightly different
// things in two tables is worse than no scale, because a reader cannot
// tell which one a given line is using.
//
// The order is deliberate -- descending by how much a result resting on
// the status can bear -- and the comparison `status <= DATASHEET` is a
// meaningful test for "solid enough to assert on".

#pragma once

namespace prov {

enum Status {
  MEASURED,    // established on OUR bench, artifact named
  DATASHEET,   // in the vendor document or the vendor header, cited, and
               // confirmed by text AND rendered page where it is a PDF
  DEFERRED,    // the document explicitly declines; it defers elsewhere
  RECALLED,    // believed vendor behaviour, NOT yet looked up here
  UNKNOWN,     // the document is ambiguous or silent; the bench decides
  ASSUMED,     // modelled one way with no evidence either way; a choice
  NOT_MODELLED // the model does NOT do this at all. A table that claims
               // behaviour the code lacks is the failure this whole
               // exercise exists to prevent, so it gets a status rather
               // than an omission
};

struct Assumption {
  Status status;
  const char* cite;    // page, header, artifact, or why there is nothing
  const char* what;
};

const char* statusName(Status s);

}  // namespace prov
