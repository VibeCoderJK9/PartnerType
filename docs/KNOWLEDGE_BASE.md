# Knowledge base

One place for the documents your partners need to sell: price lists, product sheets,
presentations, contract templates. You add a document once and every partner sees it at once in
**Knowledge base** in the left menu.

## Who can do what

| Account | List and download | Add, edit, delete |
|---|---|---|
| Administrator | yes | yes |
| Manager | yes | yes |
| Observer | yes | no |
| Partner | yes | no |

Every document is visible to every partner. There are no folders, versions or per-partner
permissions. Partners do not see who uploaded a document or how much space is used.

The database enforces the same split (row-level security, migration `0019_knowledge_base`): a
partner's or an observer's session can read the documents but cannot write, change or delete one,
whatever the request says.

## Add a document

1. **Knowledge base** in the left menu, then **Add document**.
2. Choose the file. Without a title, the file name becomes the title; change it if you like.
3. Add an optional description (shown under the title, line breaks kept) and press **Add**.

**Edit** changes the title and description. To replace the file itself, add the new one and delete
the old one. **Delete** asks first; partners stop seeing the document at once.

Partners search by title, description and file name. A document added in the last seven days
carries a **New** badge.

## Files and limits

- **Types:** PDF, Word (`.docx`), Excel (`.xlsx`), PowerPoint (`.pptx`), PNG and JPG. The type is
  read from the file's own bytes, not from its name or what the browser says; anything else is
  refused (older `.doc`/`.xls`/`.ppt`, SVG, HTML, archives, programs).
- **One file:** at most 10 MB.
- **All files together:** the space your plan includes, 1024 MB unless the operator set another
  value. The page shows how much is used; at 90% the bar turns red. When a file does not fit, the
  upload is refused with how much is used, and nothing is stored.
- **Several uploads at once:** the server takes two large uploads at a time. Another one started
  meanwhile is refused with "try again in a moment" and nothing is stored; press **Add** again.

A download is always saved as a file, never opened as a page of the application.

## For the operator

- The space per instance: `python3 -m tools.instance --env production --id vendor-a
  knowledge-quota --mb 500` (or `--default`). It reaches the app as
  `PARTNERTYPE_KNOWLEDGE_QUOTA_MB`. A value that is not a whole number from 1 to 1048576 stops
  uploads instead of guessing a limit. See [operations/INSTANCES.md](operations/INSTANCES.md).
- The files live in PostgreSQL (`knowledge_documents`), so the encrypted backup and restore carry
  them with no second storage to manage.
- Uploads, changes and deletions appear in the audit log with the title, file name and size,
  never the file's content.
