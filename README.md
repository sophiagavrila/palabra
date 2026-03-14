# palabra

Protect Word comment anchors from being silently deleted when their anchor text is edited or removed.

`palabra` wraps every `w:commentRangeStart` / `w:commentRangeEnd` pair in a locked `w:sdt` content control with a preserved space run inside, so the comment survives even if all surrounding text is deleted.

## Install

```bash
git clone <this-repo>
cd palabra
pip install -e .
```

After installation, `palabra` is available globally.

## Usage

### Protect a file

```bash
palabra protect path/to/file.docx
```

Protects all comment anchors in place. Creates a `file-comments-snapshot.json` sidecar with comment metadata.

### Watch a folder

```bash
palabra watch ~/Documents/
```

Watches recursively for `.docx` changes and auto-protects. Runs until `ctrl+c`.

### View comment history

```bash
palabra history path/to/file.docx
```

Prints a table of comments from the sidecar snapshot.

### View activity log

```bash
palabra log
```

Shows the last 50 entries from `~/.palabra/activity.log`.

## How it works

A `.docx` is a zip file. Inside `word/document.xml`, comments are anchored by `<w:commentRangeStart>` and `<w:commentRangeEnd>` elements. When a user deletes that text, Word deletes those markers and garbage-collects the comment.

`palabra protect` wraps each anchor span in a locked structured document tag (SDT):

```xml
<w:sdt>
  <w:sdtPr>
    <w:lock w:val="sdtLocked"/>
    <w:tag w:val="comment-anchor-{id}"/>
  </w:sdtPr>
  <w:sdtContent>
    <w:r><w:t xml:space="preserve"> </w:t></w:r>
    <!-- original nodes -->
  </w:sdtContent>
</w:sdt>
```

The operation is idempotent — running it twice produces no additional changes.

## Testing

```bash
pip install -e ".[dev]"
pytest test_palabra.py -v
```

## Dependencies

- `click` — CLI framework
- `watchdog` — filesystem watching
- `lxml` — XML manipulation
- `rich` — terminal output
