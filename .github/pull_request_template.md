## Change

Describe the concrete problem and resulting behavior. Link the issue if one
exists. Explain why the change belongs in the generic foundation.

## Validation

List checks that actually ran, with the tested OS/architecture. Distinguish unit
or integration tests, mock preview, native WebKit and real Bot evidence. State
unverified paths and any measured performance limits without extrapolating them.

## Boundaries and recovery

Does this change action authority, dependencies, persistence, installation or
failure recovery? Explain the effect on scoped identity, revision checks,
duplicate execution and external side effects. Include migration/rollback
guidance if saved data or installation behavior changes.

- [ ] No credentials, private chats, live databases, personal paths or proprietary bundles are included.
- [ ] Public docs and examples match the implemented behavior.
- [ ] Relevant checks passed, or their precise blocker is stated above.

Do not submit undisclosed vulnerability details in a public PR. Use the private
advisory form linked from SECURITY.md first.
