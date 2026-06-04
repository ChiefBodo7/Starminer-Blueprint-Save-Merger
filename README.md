# Starminer-Blueprint-Save-Merger
The Starminer Blueprint Save Merger can merge the selected blueprints from another save into your one.

WHAT IT DOES

- Automatically looks for your Blueprint save at:
  %LOCALAPPDATA%\ILLSpace\Saved\SaveGames\Blueprints.sav

- Loads that file as the default base when the app opens.
- Lets you add source .sav files to copy blueprints from.
- Lets you select exactly which visible blueprints to merge.
- Can save to a new file OR merge directly into AppData.
- Direct AppData merge creates:
  Blueprints.backup_before_merge.sav


SETUP

Put StarminerBlueprintMerger.exe anywhere (next to your Blueprints.sav is fine). Double-click it. No install needed. The app will read your Local AppData and load your current Blueprint.sav automatically


HOW TO USE

1. Close Starminer.
2. Open the app.
3. It should auto-load your AppData Blueprints.sav as the base.
4. Click Add source .sav files.
5. Tick/untick the blueprints you want.
6. Use either:
   - Merge selected to new file...
   - Merge selected directly into AppData Blueprints.sav

Always keep backups.


WINDOWS WARNING ("Windows protected your PC") Because this is an unsigned indie tool, Windows SmartScreen or your antivirus may warn about it. If you trust the source, click "More info" -> "Run anyway".
