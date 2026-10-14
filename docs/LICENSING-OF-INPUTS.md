# Licensing of inputs

godot-gulp converts files you already hold. It does not grant, check or change the rights you have in them.

## Your responsibility

- You must hold the rights to use and convert every input, under the terms you obtained it on (the store's EULA,
  the seller's licence, your own work).
- Converting an asset to glTF does not change its licence. The glTF, the textures and anything built from them stay
  under the input's terms.
- Some store terms tie an asset to one engine or one product. Check before you convert those:
  - Unity Asset Store: "Restricted Assets" (listed as such on the asset's page) and assets under the Unity Companion
    License need a per-asset check before use outside Unity. Ordinary (non-restricted) Asset Store assets carry no
    engine limit in the Asset Store EULA, but may not be the bulk of a product or be resold on their own.
  - Fab / Unreal Marketplace: read the licence shown on the listing (the Standard License, or a creator's own).
  - Other stores (CGTrader, Sketchfab, TurboSquid and so on): the licence on your order.
- Never automate store downloads, sign-ins or purchases with or around this tool. Download through the store's own
  client or site, as yourself.
- Reference exports made with an engine's own editor (to check this tool against): use the engine's terms for
  automation. Unity's Terms of Service (section 17.2) allow automated agents to drive Unity only through a framework
  Unity designates, so drive the Unity Editor through the official Unity CLI (shipped with Unity Hub), never with
  raw `-batchmode` scripts. Unreal Engine's editor may be run headless under its EULA; keep its outputs local.
  - Maintainers' decision (2026-10-12): agents may drive Unity through the official Unity CLI (read as Unity's
    designated route for agents), limited to importing, exporting and rendering packs the maintainer owns, with the
    outputs kept local; no Asset Store automation (downloads stay with the owner, in Unity's own client) and no use of
    Unity output for AI training.

## What godot-gulp does

- It records what you tell it. `--source-store`, `--source-licence` and `--source-id` (on `pack`, `unity` and
  `unreal`) are written to every report row and to each glTF's `asset.extras.source`, so a project's asset ledger can
  flag what needs a check (a Restricted Asset, an engine-only licence). It never guesses a licence.
- It reads file formats. No Unity source code, no Unity Companion License code, no Unreal Engine source code and no
  GPL code is included (see THIRD_PARTY.md and docs/research/).
- It bundles no proprietary decoder. Oodle-compressed Unreal payloads are read only through your own copy of the
  library (GODOT_GULP_OODLE), e.g. the one in your own Unreal Engine install.
