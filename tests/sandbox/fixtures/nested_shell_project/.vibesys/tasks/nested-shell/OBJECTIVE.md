# Nested shell command objective

Test fixture. Its only job is to declare a benchmark command whose
`--run-command-json` argument nests a second argv containing `${PROJECT_ROOT}`
and `${PACKAGE_ROOT}` tokens, so the run environment's token expansion and
shell quoting can be exercised without a candidate repository.

Nothing runs this task.
