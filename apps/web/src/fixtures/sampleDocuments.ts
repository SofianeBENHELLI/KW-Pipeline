import type { PipelineDocument } from "../domain/document";

export const sampleDocuments: PipelineDocument[] = [
  {
    id: "doc-policy-001",
    original_filename: "supplier-quality-policy.txt",
    latest_version_id: "ver-policy-002",
    created_at: "2026-04-30T08:42:00Z",
    extraction_text:
      "Supplier Quality Policy\nAll incoming supplier quality documents must include owner, scope, approval date, and source references before validation.",
    semantic: {
      document_version_id: "ver-policy-002",
      validation_status: "needs_review",
      markdown:
        "---\ntitle: Supplier Quality Policy\nstatus: needs_review\n---\n\n# Supplier Quality Policy\n\nAll incoming supplier quality documents must include owner, scope, approval date, and source references before validation.\n\n## Source Lineage\n\n- Line 2: owner, scope, approval date, and source references.",
      sections: [
        {
          title: "Supplier Quality Policy",
          level: 1,
          content:
            "All incoming supplier quality documents must include owner, scope, approval date, and source references before validation.",
          source_references: [
            {
              source_id: "source-1",
              page: null,
              line_start: 2,
              line_end: 2,
              snippet: "owner, scope, approval date, and source references",
            },
          ],
        },
      ],
    },
    versions: [
      {
        id: "ver-policy-001",
        document_id: "doc-policy-001",
        version_number: 1,
        filename: "supplier-quality-policy.txt",
        content_type: "text/plain",
        file_size: 1640,
        sha256: "39a4bdb7e4d56cbb4b54d5c18d214263a2f0e9f8c2840a8c59f33c07a39ea001",
        status: "VALIDATED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: "Initial policy validated for pilot ingestion.",
        reviewed_at: "2026-04-29T15:18:00Z",
        created_at: "2026-04-29T14:03:00Z",
      },
      {
        id: "ver-policy-002",
        document_id: "doc-policy-001",
        version_number: 2,
        filename: "supplier-quality-policy.txt",
        content_type: "text/plain",
        file_size: 1840,
        sha256: "6ad1c5de1e5a2fd3f8db4c8cfeb61a810f83f8bd3fd3f0b10d6b8e9d5875f002",
        status: "NEEDS_REVIEW",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: "2026-04-30T08:42:00Z",
      },
    ],
  },
  {
    id: "doc-failure-001",
    original_filename: "legacy-scan.pdf",
    latest_version_id: "ver-failure-001",
    created_at: "2026-04-30T07:10:00Z",
    extraction_text: "",
    semantic: null,
    versions: [
      {
        id: "ver-failure-001",
        document_id: "doc-failure-001",
        version_number: 1,
        filename: "legacy-scan.pdf",
        content_type: "application/pdf",
        file_size: 482000,
        sha256: "2d1f7a0f1a4e6a57a98cc7a693530f05f3f50e9eb0af8d12709c458f51970003",
        status: "FAILED",
        duplicate_of_version_id: null,
        failure_reason: "PlainTextParser: unsupported binary document",
        reviewer_note: null,
        reviewed_at: null,
        created_at: "2026-04-30T07:10:00Z",
      },
    ],
  },
  {
    id: "doc-validated-001",
    original_filename: "manufacturing-readiness.txt",
    latest_version_id: "ver-validated-001",
    created_at: "2026-04-29T11:25:00Z",
    extraction_text:
      "Manufacturing readiness checklist\nThe readiness gate is complete when process owner, equipment state, and acceptance criteria are recorded.",
    semantic: {
      document_version_id: "ver-validated-001",
      validation_status: "validated",
      markdown:
        "# Manufacturing readiness checklist\n\nThe readiness gate is complete when process owner, equipment state, and acceptance criteria are recorded.",
      sections: [],
    },
    versions: [
      {
        id: "ver-validated-001",
        document_id: "doc-validated-001",
        version_number: 1,
        filename: "manufacturing-readiness.txt",
        content_type: "text/plain",
        file_size: 1214,
        sha256: "7218d69a5ab6555ed3df74679febad281832007877dd1aeb000b05b9713d0004",
        status: "VALIDATED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: "Ready for MVP knowledge publication.",
        reviewed_at: "2026-04-29T13:45:00Z",
        created_at: "2026-04-29T11:25:00Z",
      },
    ],
  },
];
