# Data handling

- Do not commit credentials, tokens, private keys, customer records, or raw
  incident logs. Check the repository's visibility before adding internal links
  or sensitive system metadata; private GitHub access is not fine-grained ACL.
- Use approved authenticated tools. Do not copy credentials into prompts, URLs,
  command arguments, documentation, or reports. Do not bypass inaccessible sources.
- Scope log/trace queries by time and target; minimize collection and redact
  credentials and identifying data before reporting. Browser HAR files and
  request headers may contain secrets; do not attach raw captures.
- Link to access-controlled incident evidence instead of duplicating it here.
- Keep cached content within its original access boundary. Do not upload private
  documentation to external search/indexing services without authorization.
- If sensitive information is exposed, stop reproducing it and follow the
  organization's security response process; ask for that process if unknown.
