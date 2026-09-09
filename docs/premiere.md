# Premiere XML export and acceptance checklist

## Format choice

V1 writes **legacy Final Cut Pro XML**, root `<xmeml version="5">`. This is not modern
`.fcpxml` and not Premiere's native `.prproj` format. Adobe documents that modern FCP X XML
cannot be imported directly without conversion. The relevant interchange model is documented
in Apple's archived xmeml reference.

- [Adobe: importing FCP XML](https://helpx.adobe.com/premiere/desktop/organize-media/import-files/migrate-from-final-cut-pro-x.html)
- [Apple: xmeml elements and timing](https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/FinalCutPro_XML/Elements/Elements.html)

The serializer includes original file URLs, sequence and source frame rates, in/out points,
video, original mono/stereo audio, optional mono/stereo music, linked components, descriptive
clip names and sequence markers. Basic Motion scale is emitted to fit the source into the
sequence; cropping and subject tracking are not implemented.

## What was and was not tested

Automated tests check well-formed XML, file references, linked clip identifiers, integer timing,
source bounds, quantization, immutable plans, and media preservation. **They do not execute
Premiere or prove that its importer interprets every field as intended.**

The normal export path requires source and sequence FPS to match. Mixed FPS, suspected VFR,
and significant source A/V offsets require `--allow-unverified-timing`. This flag only bypasses
a conservative gate for experimentation; it does not fix VFR or certify synchronization.
Surround/multiple audio streams are explicitly unsupported in this release.

FFprobe metadata is only a VFR warning heuristic. A file can have variable timestamps even
when its reported average and nominal rates agree. Smartphone footage must be checked in
Premiere; automatic full timestamp scanning/conforming is a future task.

## First import

1. Run the synthetic demo, then import its `exports/cut-0001.xml` with **File > Import**.
2. Confirm the sequence opens, media links resolve, the duration is 8 seconds at 24 fps,
   there are three video edits, and linked source audio is present where expected.
3. Check the first and last frame of each cut, audio alignment, letterboxing/scale and the
   ability to extend a clip into unused original media.
4. Save a native `.prproj`. The XML is an interchange artifact, not a replacement for a saved
   Premiere project. Importing it normally also resolves referenced media; separately importing
   all videos first is not required by this application.

Then test a short real file from each camera separately at its actual frame rate. Only after
those pass should mixed DJI/phone material be tested. Record exact Premiere, runtime and source
format versions; mark compatibility as verified only for combinations actually exercised.

## Real mixed-camera trial

```powershell
.\.venv\Scripts\autoeditor.exe export .\work\route --allow-unverified-timing
```

If a source average rate cannot be represented in xmeml (for example
`742343/24665`), the plan is still saved. For a manual import trial, explicitly choose
the rate at which **all source in/out times** should be represented:

```powershell
.\.venv\Scripts\autoeditor.exe export .\work\route --allow-unverified-timing --source-fps 30
```

This is an XML timing interpretation only. It does not transcode or conform VFR, change
originals, modify the saved plan, or rerun analysis. The original measured/nominal rates
and the chosen XML rates are recorded in `export_validation.source_rate_interpretations`.
An explicit override always requires the experimental flag, even if it matches the sequence.
Source video and linked source audio use the same interpreted rate; music keeps the
sequence rate. Check cut boundaries, playback speed and audio synchronization in Premiere.
If the trial drifts, conforming separate copies or timestamp-aware mapping remains necessary.

Review all timing warnings in the companion JSON. If importing at a different drive letter:

```powershell
.\.venv\Scripts\autoeditor.exe relink .\work\route --media-root "F:\Videos\Route"
.\.venv\Scripts\autoeditor.exe export .\work\route --overwrite --allow-unverified-timing
```

`relink` hashes replacement originals to prevent linking a plan to unrelated same-named files.
The new XML has current file URLs. Existing Premiere projects may also need their own media
relink operation.

## Color, exposure and audio

The exporter does not normalize HDR, D-Log M, HLG or different camera color profiles. The model
sees small preview frames without a dedicated color-management pipeline, so technical judgments
on log/HDR footage can be misleading. Review transformations and color space settings in Premiere.

Premiere's color controls are not a promise of advanced temporal video denoising. This project
adds no denoiser and should not be advertised as rescuing low-light action-camera footage.

Original audio and music remain separate and are not automatically mixed, faded or ducked.
A loud music/source combination can clip until the mix is adjusted. No speed ramps, motion
interpolation, stabilization, creative filters or transitions are baked into the rough cut.

## Future interoperability work

Validate real 24/25/30/50/60/120 and NTSC-rate samples in Premiere, implement a full timestamp
check for VFR, test rotated phone videos, source timecodes and mixed-rate edge cases, then add
safe optional conforming if necessary. Do not replace this gate with an untested compatibility claim.
