# TrendForceDash's pipeline is scheduled via launchd, not cron - see the
# four com.elainekao.trendforcedash-*.plist files in
# ~/Library/LaunchAgents/. Migrated from cron because launchd catches up
# a missed StartCalendarInterval run shortly after the Mac wakes from
# sleep (e.g. lid closed through a scheduled time); cron just silently
# skips it until the next scheduled slot.
#
# Jobs (all run run_pipeline.sh, output appended to pipeline.log):
#   com.elainekao.trendforcedash-scan      scan every 4h  (0/4/8/12/16/20h)
#   com.elainekao.trendforcedash-core      core every 6h  (0/6/12/18h)
#   com.elainekao.trendforcedash-accounts  accounts every 8h (0/8/16h)
#   com.elainekao.trendforcedash-daily     daily once/day (07:00)
#
# To (re)install after editing a plist:
#   plutil -lint ~/Library/LaunchAgents/com.elainekao.trendforcedash-<job>.plist
#   launchctl bootout gui/$(id -u)/com.elainekao.trendforcedash-<job>
#   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.elainekao.trendforcedash-<job>.plist
#
# To check status: launchctl list | grep trendforcedash
# (first column is PID if currently running, second is last exit code)
