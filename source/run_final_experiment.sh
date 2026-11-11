#!/usr/bin/env bash
# Collect the final experiment's data: the standard study on AWGN and five fading channels, one after another.
#
# Usage: ./run_final_experiment.sh [extra run_study.sh options]
#
# Each channel writes results/final-<channel>/ and its full output to results/final-logs/<channel>.log; this script
# prints only overall progress. The channels share one seed, separate from the quick runs', so this is new data.
# Running it again resumes: finished trials are skipped. Right after a resume the overall ETA reads too low, until
# enough new work has been done. Options given here are passed on and override the defaults.
set -euo pipefail

readonly CHANNELS=(AWGN MM MQ MD HM LM)
readonly PRESET=standard
readonly WORKERS=16
readonly NICENESS=19
readonly SEED=2027
readonly RUN_PREFIX=final
readonly PROGRESS_INTERVAL_S=60
readonly STOP_GRACE_S=15

# Rough share of one channel's run time spent in each stage, in percent; they set the overall percentage and ETA.
readonly PILOT_WEIGHT=10
readonly FINE_WEIGHT=80
readonly NOISE_WEIGHT=10

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tree="$(cd "$here/../.." && pwd)"
log_dir="${FINAL_LOG_DIR:-$here/results/final-logs}"


# Print a number of seconds as a short duration such as 2h05m.
format_duration() {
    local seconds=$1

    if ((seconds >= 3600)); then
        printf '%dh%02dm' $((seconds / 3600)) $((seconds % 3600 / 60))
    else
        printf '%dm%02ds' $((seconds / 60)) $((seconds % 60))
    fi
}


# Build the codec once, at low priority and within the core limit, so no run_study.sh rebuilds it with every core.
build_codec() {
    if [[ -n "${WSJT_CODEC:-}" ]]; then
        return
    fi

    if [[ ! -f "$tree/build/CMakeCache.txt" ]]; then
        echo "No configured WSJT-X build at $tree/build; configure it with CMake first, or set WSJT_CODEC." >&2
        exit 1
    fi

    echo "Building wsjt-codec (incremental)..."
    nice -n "$NICENESS" cmake --build "$tree/build" --target wsjt-codec -j"$WORKERS" >/dev/null
    export WSJT_CODEC="$tree/build/wsjt-codec"
}


# Run every channel in turn, emitting @@ markers for report_progress around each run's output. Stops at a failure.
run_channels() {
    local index=0 channel

    for channel in "${CHANNELS[@]}"; do
        index=$((index + 1))
        echo "@@start $index $channel"

        if ! nice -n "$NICENESS" "$here/run_study.sh" --preset "$PRESET" --workers "$WORKERS" --seed "$SEED" \
            --channel "$channel" --run-name "$RUN_PREFIX-$channel" "$@" 2>&1 | tee -ai "$log_dir/$channel.log"; then
            echo "@@failed $index $channel"
            return 1
        fi

        echo "@@done $index $channel"
    done
}


# Read run_channels' output and print overall progress: at most once per interval, and at every milestone.
report_progress() {
    local -r total=${#CHANNELS[@]}
    local -r started=$EPOCHSECONDS
    local line channel="" index=0 stage="" detail="" last_print=0
    local pilot=0 fine=0 noise=0 skipped=0 finished=0

    # Keep reading through a stop request, so the run's last output still reaches the log and the screen.
    trap '' INT

    # Print the overall line. Fractions are in units of 1/100000 so bash's integer arithmetic is enough.
    print_status() {
        local -r channel_part=$((PILOT_WEIGHT * pilot + FINE_WEIGHT * fine + NOISE_WEIGHT * noise))
        local -r overall=$(((finished * 100000 + channel_part) / total))
        local -r elapsed=$((EPOCHSECONDS - started))
        local -r worked=$((overall - skipped))
        local eta="unknown"

        # Below 0.5% of real work (for example a resume that has only skipped finished stages) an estimate is noise.
        if ((worked >= 500)); then
            eta=$(format_duration $((elapsed * (100000 - overall) / worked)))
        fi

        printf '[final %(%H:%M:%S)T] overall %d.%d%% | channel %d/%d %s | %s | elapsed %s | ETA %s\n' -1 \
            $((overall / 1000)) $((overall % 1000 / 100)) "$index" "$total" "$channel" "$detail" \
            "$(format_duration "$elapsed")" "$eta"
        last_print=$EPOCHSECONDS
    }

    # Record that ``stage`` is ``permille``/1000 done, never moving backward (a pilot extension round restarts its count).
    record_stage() {
        local -r name=$1 permille=$2

        case "$name" in
            pilot) ((permille > pilot)) && pilot=$permille ;;
            fine) ((permille > fine)) && fine=$permille ;;
            noise) ((permille > noise)) && noise=$permille ;;
            fixed)
                ((permille > pilot)) && pilot=$permille
                ((permille > fine)) && fine=$permille
                ;;
        esac

        return 0
    }

    while IFS= read -r line; do
        if [[ $line =~ ^@@start\ ([0-9]+)\ (.+)$ ]]; then
            index=${BASH_REMATCH[1]}
            channel=${BASH_REMATCH[2]}
            pilot=0 fine=0 noise=0 detail="starting"
            print_status
        elif [[ $line =~ ^@@done\  ]]; then
            finished=$index
            pilot=0 fine=0 noise=0 detail="channel finished"
            print_status
        elif [[ $line =~ ^@@failed\  ]]; then
            detail="STOPPED (failed or interrupted); see $log_dir/$channel.log"
            print_status
            tail -n 30 "$log_dir/$channel.log"
        elif [[ $line =~ ^\[(pilot|fine|fixed|noise)\]\ ([0-9]+)/([0-9]+)\ ([a-z]+),\ ([0-9.]+)/s,\ ([0-9]+)\ errors ]]; then
            stage=${BASH_REMATCH[1]}
            local count=${BASH_REMATCH[2]} all=${BASH_REMATCH[3]}
            record_stage "$stage" $((count * 1000 / all))
            detail="$stage $count/$all ${BASH_REMATCH[4]}, ${BASH_REMATCH[5]}/s, ${BASH_REMATCH[6]} errors"

            if ((count == all || EPOCHSECONDS - last_print >= PROGRESS_INTERVAL_S)); then
                print_status
            fi
        elif [[ $line =~ ^\[(pilot|fine|fixed|noise)\]\ nothing\ to\ do ]]; then
            # Already collected in an earlier run: it took no time now, so it must not make the ETA look faster.
            stage=${BASH_REMATCH[1]}
            # A pilot with extension rounds prints this once per round, so count each stage only once per channel.
            local weight=$NOISE_WEIGHT recorded=$noise
            case "$stage" in
                pilot) weight=$PILOT_WEIGHT recorded=$pilot ;;
                fine) weight=$FINE_WEIGHT recorded=$fine ;;
                fixed) weight=$((PILOT_WEIGHT + FINE_WEIGHT)) recorded=$fine ;;
            esac
            if ((recorded < 1000)); then
                skipped=$((skipped + weight * (1000 - recorded) / total))
            fi
            record_stage "$stage" 1000
        fi
    done
}


main() {
    mkdir -p "$log_dir"
    build_codec

    # The run gets its own process group, and any stop signal is passed to the whole group as an interrupt, so stopping
    # this script (Ctrl-C, kill, hangup) stops every worker and no later channel starts behind its back.
    local job status=0 stop_requested=0 deadline
    set -m
    { run_channels "$@" | report_progress; } </dev/null &
    job=$!
    trap 'stop_requested=1; kill -INT -- "-$job" 2>/dev/null || true' INT TERM HUP

    while kill -0 "$job" 2>/dev/null; do
        status=0
        wait "$job" || status=$?

        if ((stop_requested)); then
            # The worker pool can hang after an interrupt, so end the group once the grace period is over. Rows are
            # saved per finished batch of trials, so this loses only the batches that were in progress. The sleep runs
            # in the background so it never takes the terminal: a second Ctrl-C must not end this loop early.
            deadline=$((EPOCHSECONDS + STOP_GRACE_S))
            while kill -0 -- "-$job" 2>/dev/null && ((EPOCHSECONDS < deadline)); do
                sleep 1 &
                wait "$!" || true
            done
            kill -KILL -- "-$job" 2>/dev/null || true
            wait "$job" 2>/dev/null || true
            status=130
            break
        fi
    done

    if ((status != 0)); then
        echo "Final experiment stopped before finishing. Run this script again to resume."
        return "$status"
    fi

    echo "Final experiment finished: ${CHANNELS[*]}. Reports are in results/$RUN_PREFIX-<channel>/report.html."
}


main "$@"
