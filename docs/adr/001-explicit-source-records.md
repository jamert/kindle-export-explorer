# ADR 001: Explicit Source Records

- **Status:** Planned
- **Date:** 2026-10-11

## Context

After significant progress using a more AI-delegated approach to implementation, the time has come to improve the internal structure of the application.
The verification loop focused on the application's boundary — running CLI commands against my Kindle export archive produced answers in the expected shape, and sometimes preferences about internal structure were voiced (like having `CanonicalKey` classes), but many decisions on code organization were left to the model.
As usual (at this point in time), that produced many signs of technical debt:
- `clean` helpers used on every stage of data processing,
- lack of code separation between stages of processing,
- spreading decisions related to one datum into different places in the codebase.

The problems were uncovered in an attempt to enforce layering in the codebase and harmonize the filtering.
The `SourceRecordCatalog` takes raw information in and produces canonical records, but the connection to the source data becomes muddled in the process (it's spread through a lot of disconnected statements).

We have a lot of different areas of technical quality to address, but we need to focus on one thing at a time.
The specific misfit this decision targets is: _it should be easy to understand which files and fields in the source data are used by the application_.

The latest refactoring has shown that making a separate class for each type of source record is a viable approach.
The _"source record"_ in the context of this project means that a _structured_ class instance is produced from _one_ record of _one_ dataset from the export.
So the core of the term is a one-to-one connection with a specific line from some file, but it also needs to be an object where field access is done through explicit fields.
By _dataset_ I mean one file or collection of files containing data describing the same kind of entity or relationship.
A source record definition allows transformation of original fields, including omitting some of the original fields and splitting or combining original fields.
But it prohibits some operations, like joins and enrichment using other datasets.
Having a separate description of source data and its parsing will clarify all processes before canonicalization.

The decision also affects filtering invalid records, which is currently done in an ad hoc manner.
Non-conforming records (like those without an ID) can be filtered at this stage.

Of course, the current manual approach that was applied to the `Digital.Content.Ownership` dataset is not optimal.
First of all, fields are not yet converted to native Python types, like `datetime`.
Strict typing with Pyright forces a lot of extra boilerplate.
And it's not yet clear what are the rules to skip records because they are not useful later (because of non-recoverable ID).
But it's a step in the right direction (closer to clear separation of layers).

## Decision

Before combining data into canonical records, a source record layer will be introduced.
It will cover parsing of individual datasets in terms of field selection, transformation, type conversion, and filtering of invalid source records.
Most likely, the layer will be implemented using a small custom declarative parser framework, but the details will be ironed out in the next ADR.

## Consequences

Each record will need some boilerplate in the form of classes and a declarative parser description, but the parsing and filtering decisions related to record validation should become more explicit.
