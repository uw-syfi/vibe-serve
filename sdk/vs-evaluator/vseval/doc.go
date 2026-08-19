// Package vseval writes VibeSys evaluator result streams.
//
// An evaluator declares the metrics it produces, measures them, and reports a
// single row of values. The report is a record stream: one JSON object per
// line, written to the file named by the -vs-output flag. The wire format is
// specified by sdk/vs-evaluator/PROTOCOL.md.
//
// Reporting has two phases, because the two are not always known at the same
// time. Opening the stream needs only the output path, which comes from the
// argv; declaring the schema needs the metric names, which an evaluator may
// have to read from a workload config first. Opening early is what lets a
// failure in between still reach the framework as a reason:
//
//	open -> declare -> set -> emit
//	  |        |        |
//	  +--------+--------+---> fail
//
// Most evaluators are multi-command programs whose subcommands parse their own
// argv, so the typical entry point is [OpenFlagSet]:
//
//	func benchmark(args []string) error {
//		fs := flag.NewFlagSet("benchmark", flag.ContinueOnError)
//		workload := fs.String("workload", "", "path of the workload config")
//
//		// Registers -vs-output on fs, parses args, opens the output.
//		report, err := vseval.OpenFlagSet(fs, args)
//		if err != nil {
//			return err
//		}
//		defer report.Close()
//
//		config, err := loadWorkload(*workload)
//		if err != nil {
//			// No schema exists yet. An error record on its own is a valid
//			// stream, so the framework still learns why the run failed.
//			return errors.Join(err, report.EmitError(err))
//		}
//
//		direction, err := vseval.ParseDir(config.Direction) // "maximize"
//		if err != nil {
//			return errors.Join(err, report.EmitError(err))
//		}
//		schema := vseval.NewSchema()
//		primary := schema.Number(config.Metric, vseval.Unit(config.Unit), vseval.Direction(direction))
//		// Reported only when a request actually completed.
//		p99 := schema.Number("p99_latency_ms", vseval.Unit("ms"), vseval.Direction(vseval.Min), vseval.Optional())
//
//		// Writes and flushes the hello record.
//		run, err := report.Declare(schema)
//		if err != nil {
//			return errors.Join(err, report.EmitError(err))
//		}
//
//		result, err := measure(config)
//		if err != nil {
//			// Report the failure on the stream and still return it, so the
//			// process exits non-zero.
//			return errors.Join(err, run.EmitError(err))
//		}
//		run.Set(primary, result.Throughput)
//		if result.Completed > 0 {
//			run.Set(p99, result.P99Millis)
//		}
//		return run.Emit()
//	}
//
// The caller owns nothing it must remember except the deferred [Report.Close]:
// [Run.Emit] and [Run.EmitError] write a record and flush it, and leave
// closing to the deferred call.
//
// Reporting is optional. When -vs-output is absent the returned report
// discards every record, so the same code path works for a standalone
// invocation; [Report.Reporting] tells an evaluator whether a report was
// actually requested.
//
// [Run.Set] takes a [Metric] handle rather than a name, so a metric that was
// never declared is a compile error rather than a run-time surprise, and a
// result can only be emitted through the [Run] that declaring returns.
//
// An evaluator with a single command and no flag set of its own can use
// [Open], which does the same against flag.CommandLine. One that parses its
// own flags can use [OpenPath] with the path it parsed.
package vseval
