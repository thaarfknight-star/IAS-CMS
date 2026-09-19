; ============================================================
; نصب‌کننده‌ی «ایمن آرا سورنا» (IAS-CMS)
; رابط کاربری کاملاً اختصاصی (تک‌صفحه‌ی nsDialogs با ۴ مرحله):
;   خوش‌آمد → انتخاب پوشه → نصب (نوار پیشرفت واقعی) → پایان
; کامپایل (روی Windows runner):
;   makensis /DVERSION=2.0.0 installer/installer.nsi
; ============================================================
Unicode True
RequestExecutionLevel user
XPStyle on

!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!include "StrFunc.nsh"
${StrStr}

; ---------- defines ----------
!define APP_NAME "ایمن آرا سورنا"
!define APP_EN "IAS-CMS"
!define EXE_NAME "CCTV_CMS.exe"
!define PUBLISHER "ایمن آرا سورنا"

!ifndef VERSION
  !define VERSION "2.0.0"
!endif
!ifndef DISTDIR
  !define DISTDIR "dist"
!endif
!ifndef GFXDIR
  !define GFXDIR "installer/graphics"
!endif
!ifndef OUTDIR
  !define OUTDIR "dist"
!endif
!ifndef ROOTDIR
  !define ROOTDIR "."
!endif

Name "${APP_NAME} v${VERSION}"
Caption "نصب ${APP_NAME} نسخه‌ی ${VERSION}"
!ifdef UNINSTALLER_ONLY
  ; --- حالت حذف‌کننده‌ی مستقل: فقط پاک‌سازی کامل، بدون صفحه‌ی نصب ---
  OutFile "${OUTDIR}\IAS-CMS-Uninstall-v${VERSION}.exe"
  Caption "حذف کامل ${APP_NAME}"
!else
  OutFile "${OUTDIR}\IAS-CMS-Setup-v${VERSION}.exe"
!endif
Icon "${ROOTDIR}\assets\app.ico"
InstallDir "$LOCALAPPDATA\ImenaraSorena\IAS-CMS"
; خواندن محل واقعی نصب از رجیستری — اگر کاربر پوشه را عوض کرده باشد،
; حذف‌کننده همان مسیر واقعی را پیدا می‌کند (نه مسیر پیش‌فرض)
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_EN}" "InstallLocation"
ShowInstDetails nevershow

; ---------- ثابت‌های Win32 از WinMessages.nsh/WinCore.nsh می‌آیند ----------
; ---------- متغیرها ----------
Var Dialog
Var BgCtl
Var BgBmp
Var BgFile
Var BtnNext
Var BtnBack
Var BtnCancel
Var BtnBrowse
Var DirEdit
Var ProgressBar
Var StatusLabel
Var RunCheck
Var Ctl0
Var Ctl1
Var Ctl2
Var Ctl3
Var Ctl4
Var Ctl5
Var Ctl6
Var Ctl7
Var Ctl8
Var Ctl9

; ============================================================
; ابزارهای پایه‌ی UI (ورودی‌ها با Push، خروجی با Pop — متوازن)
; ============================================================
Function SetClientSize
  ; Push w, Push h → ناحیه‌ی مشتری پنجره دقیقاً w×h پیکسل می‌شود
  Pop $R0 ; w
  Pop $R1 ; h
  System::Call 'user32::GetWindowRect(i $HWNDPARENT, @r9)'
  System::Call '*$9(i.r2, i.r3, i.r4, i.r5)'
  IntOp $R2 $R4 - $R2   ; outer w
  IntOp $R3 $R5 - $R3   ; outer h
  System::Call 'user32::GetClientRect(i $HWNDPARENT, @r9)'
  System::Call '*$9(i.r2, i.r3, i.r4, i.r5)'
  IntOp $R4 $R4 - $R2   ; client w
  IntOp $R5 $R5 - $R3   ; client h
  IntOp $R2 $R2 - $R4
  IntOp $R2 $R2 + $R0   ; outer w جدید
  IntOp $R3 $R3 - $R5
  IntOp $R3 $R3 + $R1   ; outer h جدید
  System::Call 'user32::GetSystemMetrics(i 0) i.s'
  Pop $R4
  System::Call 'user32::GetSystemMetrics(i 1) i.s'
  Pop $R5
  IntOp $R4 $R4 - $R2
  IntOp $R4 $R4 / 2
  IntOp $R5 $R5 - $R3
  IntOp $R5 $R5 / 2
  System::Call 'user32::SetWindowPos(i $HWNDPARENT, i 0, i $R4, i $R5, i $R2, i $R3, i 0x14)'
FunctionEnd

Function HideWizardButtons
  GetDlgItem $R0 $HWNDPARENT 1
  System::Call 'user32::ShowWindow(i $R0, i ${SW_HIDE})'
  GetDlgItem $R0 $HWNDPARENT 2
  System::Call 'user32::ShowWindow(i $R0, i ${SW_HIDE})'
  GetDlgItem $R0 $HWNDPARENT 3
  System::Call 'user32::ShowWindow(i $R0, i ${SW_HIDE})'
FunctionEnd

Function TrackCtl
  Pop $R0 ; hwnd
  ${If} $Ctl0 == 0
    StrCpy $Ctl0 $R0
  ${ElseIf} $Ctl1 == 0
    StrCpy $Ctl1 $R0
  ${ElseIf} $Ctl2 == 0
    StrCpy $Ctl2 $R0
  ${ElseIf} $Ctl3 == 0
    StrCpy $Ctl3 $R0
  ${ElseIf} $Ctl4 == 0
    StrCpy $Ctl4 $R0
  ${ElseIf} $Ctl5 == 0
    StrCpy $Ctl5 $R0
  ${ElseIf} $Ctl6 == 0
    StrCpy $Ctl6 $R0
  ${ElseIf} $Ctl7 == 0
    StrCpy $Ctl7 $R0
  ${ElseIf} $Ctl8 == 0
    StrCpy $Ctl8 $R0
  ${ElseIf} $Ctl9 == 0
    StrCpy $Ctl9 $R0
  ${EndIf}
FunctionEnd

Function DestroyOneCtl
  Pop $R0 ; hwnd
  ${If} $R0 != 0
    System::Call 'user32::DestroyWindow(i $R0)'
  ${EndIf}
FunctionEnd

Function ClearScreen
  Push $Ctl0
  Call DestroyOneCtl
  Push $Ctl1
  Call DestroyOneCtl
  Push $Ctl2
  Call DestroyOneCtl
  Push $Ctl3
  Call DestroyOneCtl
  Push $Ctl4
  Call DestroyOneCtl
  Push $Ctl5
  Call DestroyOneCtl
  Push $Ctl6
  Call DestroyOneCtl
  Push $Ctl7
  Call DestroyOneCtl
  Push $Ctl8
  Call DestroyOneCtl
  Push $Ctl9
  Call DestroyOneCtl
  StrCpy $Ctl0 0
  StrCpy $Ctl1 0
  StrCpy $Ctl2 0
  StrCpy $Ctl3 0
  StrCpy $Ctl4 0
  StrCpy $Ctl5 0
  StrCpy $Ctl6 0
  StrCpy $Ctl7 0
  StrCpy $Ctl8 0
  StrCpy $Ctl9 0
  ${If} $BgBmp != 0
    System::Call 'gdi32::DeleteObject(i $BgBmp)'
    StrCpy $BgBmp 0
  ${EndIf}
FunctionEnd

Function PlaceCtl
  ; Push hwnd, x, y, w, h (پیکسل)
  Pop $R0 ; h
  Pop $R1 ; w
  Pop $R2 ; y
  Pop $R3 ; x
  Pop $R4 ; hwnd
  System::Call 'user32::SetWindowPos(i $R4, i 0, i $R3, i $R2, i $R1, i $R0, i 0x16)'
FunctionEnd

Function ShowBackground
  ; ورودی: نام فایل BMP روی استک (مثلاً "bg_welcome.bmp").
  ; نکته: اسم فایل در $BgFile (متغیر اختصاصی) نگه داشته می‌شود، چون
  ; TrackCtl و PlaceCtl رجیسترهای $R0 تا $R4 را بازنویسی می‌کنند.
  Pop $BgFile
  nsDialogs::CreateControl "STATIC" "${SS_BITMAP}|${WS_CHILD}|${WS_VISIBLE}" 0 0 0 10 10 ""
  Pop $BgCtl
  Push $BgCtl
  Call TrackCtl
  Push $BgCtl
  Push 0
  Push 0
  Push 960
  Push 600
  Call PlaceCtl
  System::Call 'user32::LoadImage(i 0, t "$PLUGINSDIR\$BgFile", i ${IMAGE_BITMAP}, i 0, i 0, i ${LR_LOADFROMFILE}) i.s'
  Pop $BgBmp
  SendMessage $BgCtl ${STM_SETIMAGE} ${IMAGE_BITMAP} $BgBmp
FunctionEnd

Function MakeButton
  ; Push "متن" → دکمه‌ی اصلی (آبی تخت)؛ هندل روی استک برمی‌گردد
  Pop $R0
  nsDialogs::CreateControl "BUTTON" "${BS_PUSHBUTTON}|${BS_FLAT}|${WS_CHILD}|${WS_VISIBLE}" 0 0 0 10 10 "$R0"
  Pop $R1
  Push $R1
  Call TrackCtl
  SetCtlColors $R1 "FFFFFF" "0F7CC1"
  Push $R1
FunctionEnd

Function MakeGhostButton
  ; Push "متن" → دکمه‌ی ثانویه (تیره)
  Pop $R0
  nsDialogs::CreateControl "BUTTON" "${BS_PUSHBUTTON}|${BS_FLAT}|${WS_CHILD}|${WS_VISIBLE}" 0 0 0 10 10 "$R0"
  Pop $R1
  Push $R1
  Call TrackCtl
  SetCtlColors $R1 "E9EEF1" "242E34"
  Push $R1
FunctionEnd

; ============================================================
; صفحه‌ها (۴ مرحله در یک Page custom)
; ============================================================
Function ShowWelcome
  Push "bg_welcome.bmp"
  Call ShowBackground
  Push "شروع نصب"
  Call MakeButton
  Pop $BtnNext
  Push $BtnNext
  Push 700
  Push 506
  Push 220
  Push 44
  Call PlaceCtl
  ${NSD_OnClick} $BtnNext OnWelcomeNext
  Push "انصراف"
  Call MakeGhostButton
  Pop $BtnCancel
  Push $BtnCancel
  Push 40
  Push 506
  Push 150
  Push 44
  Call PlaceCtl
  ${NSD_OnClick} $BtnCancel OnCancel
FunctionEnd

Function OnWelcomeNext
  Call ClearScreen
  Call ShowDir
  System::Call 'user32::UpdateWindow(i $Dialog)'
FunctionEnd

Function ShowDir
  Push "bg_dir.bmp"
  Call ShowBackground
  nsDialogs::CreateControl "EDIT" "${ES_LEFT}|${ES_AUTOHSCROLL}|${WS_CHILD}|${WS_VISIBLE}" 0 0 0 10 10 "$INSTDIR"
  Pop $DirEdit
  Push $DirEdit
  Call TrackCtl
  SetCtlColors $DirEdit "E9EEF1" "141B20"
  Push $DirEdit
  Push 96
  Push 314
  Push 600
  Push 32
  Call PlaceCtl
  Push "…"
  Call MakeGhostButton
  Pop $BtnBrowse
  Push $BtnBrowse
  Push 706
  Push 310
  Push 120
  Push 40
  Call PlaceCtl
  ${NSD_OnClick} $BtnBrowse OnBrowse
  Push "نصب"
  Call MakeButton
  Pop $BtnNext
  Push $BtnNext
  Push 700
  Push 506
  Push 220
  Push 44
  Call PlaceCtl
  ${NSD_OnClick} $BtnNext OnDirNext
  Push "بازگشت"
  Call MakeGhostButton
  Pop $BtnBack
  Push $BtnBack
  Push 210
  Push 506
  Push 150
  Push 44
  Call PlaceCtl
  ${NSD_OnClick} $BtnBack OnDirBack
  Push "انصراف"
  Call MakeGhostButton
  Pop $BtnCancel
  Push $BtnCancel
  Push 40
  Push 506
  Push 150
  Push 44
  Call PlaceCtl
  ${NSD_OnClick} $BtnCancel OnCancel
FunctionEnd

Function OnBrowse
  nsDialogs::SelectFolderDialog "انتخاب پوشه‌ی نصب ${APP_NAME}" "$INSTDIR"
  Pop $R0
  ${If} $R0 != "error"
  ${AndIf} $R0 != ""
    ${NSD_SetText} $DirEdit $R0
  ${EndIf}
FunctionEnd

Function OnDirBack
  Call ClearScreen
  Call ShowWelcome
  System::Call 'user32::UpdateWindow(i $Dialog)'
FunctionEnd

Function CheckAppRunning
  ; خروجی در $R0: ۱ اگر برنامه در حال اجراست
  nsExec::ExecToStack '"$SYSDIR\tasklist.exe" /FI "IMAGENAME eq ${EXE_NAME}" /FO CSV /NH'
  Pop $R1
  Pop $R2
  ${StrStr} $R0 $R2 "${EXE_NAME}"
  ${If} $R0 == ""
    StrCpy $R0 0
  ${Else}
    StrCpy $R0 1
  ${EndIf}
FunctionEnd

Function OnDirNext
  ${NSD_GetText} $DirEdit $R0
  ${If} $R0 == ""
    MessageBox MB_ICONEXCLAMATION "لطفاً پوشه‌ی نصب را مشخص کنید."
    Return
  ${EndIf}
  StrCpy $INSTDIR $R0
  ; اگر برنامه باز است، از کاربر بخواه ببندد
  CheckLoop:
    Call CheckAppRunning
    ${If} $R0 == 1
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
        "برنامه‌ی ${APP_NAME} در حال اجراست.$\nلطفاً آن را ببندید و «تلاش مجدد» را بزنید." \
        IDRETRY CheckLoop IDCANCEL CancelInstall
      CancelInstall:
        Return
    ${EndIf}
  ; فضای دیسک: اگر موقع کپی جا کم بیاید، DoInstall خطا برمی‌گرداند
  ; رفتن به صفحه‌ی نصب و اجرای نصب
  Call ClearScreen
  Call ShowInstall
  System::Call 'user32::UpdateWindow(i $Dialog)'
  Call DoInstall
  ${If} $R9 == "ok"
    Call ClearScreen
    Call ShowFinish
    System::Call 'user32::UpdateWindow(i $Dialog)'
  ${Else}
    MessageBox MB_ICONSTOP "نصب ناقص ماند.$\n$R9"
    Call ClearScreen
    Call ShowDir
    System::Call 'user32::UpdateWindow(i $Dialog)'
  ${EndIf}
FunctionEnd

Function ShowInstall
  Push "bg_install.bmp"
  Call ShowBackground
  nsDialogs::CreateControl "msctls_progress32" "${WS_CHILD}|${WS_VISIBLE}" 0 0 0 10 10 ""
  Pop $ProgressBar
  Push $ProgressBar
  Call TrackCtl
  Push $ProgressBar
  Push 80
  Push 308
  Push 800
  Push 28
  Call PlaceCtl
  SendMessage $ProgressBar ${PBM_SETRANGE} 0 0x640000
  SendMessage $ProgressBar ${PBM_SETBARCOLOR} 0 0xC17C0F
  nsDialogs::CreateControl "STATIC" "${SS_LEFT}|${WS_CHILD}|${WS_VISIBLE}" 0 0 0 10 10 "آماده‌سازی…"
  Pop $StatusLabel
  Push $StatusLabel
  Call TrackCtl
  SetCtlColors $StatusLabel "E9EEF1" "141B20"
  Push $StatusLabel
  Push 80
  Push 366
  Push 800
  Push 28
  Call PlaceCtl
FunctionEnd

Function DoInstall
  StrCpy $R9 ""
  ${NSD_SetText} $StatusLabel "در حال ساخت پوشه‌ها…"
  System::Call 'user32::UpdateWindow(i $StatusLabel)'
  CreateDirectory "$INSTDIR"
  ${If} ${Errors}
    StrCpy $R9 "ساخت پوشه‌ی نصب ممکن نشد: $INSTDIR"
    Return
  ${EndIf}
  ; کپی فایل‌ها (chunkبندی‌شده با نوار پیشرفت واقعی — تولیدشده توسط gen_filelist.py)
  !include "${ROOTDIR}\installer\files.nsi"
  ${If} ${Errors}
    StrCpy $R9 "خطا در کپی فایل‌ها."
    Return
  ${EndIf}
  ${NSD_SetText} $StatusLabel "در حال ساخت میان‌برها…"
  System::Call 'user32::UpdateWindow(i $StatusLabel)'
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" \
    "$INSTDIR\${EXE_NAME}" "" "$INSTDIR\${EXE_NAME}" 0
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\حذف برنامه.lnk" "$INSTDIR\uninstall.exe"
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" \
    "$INSTDIR\${EXE_NAME}" "" "$INSTDIR\${EXE_NAME}" 0
  WriteUninstaller "$INSTDIR\uninstall.exe"
  ; اطلاعات حذف در رجیستری کاربر جاری (بدون نیاز به ادمین)
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_EN}" \
    "DisplayName" "${APP_NAME} (${APP_EN})"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_EN}" \
    "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_EN}" \
    "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_EN}" \
    "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_EN}" \
    "DisplayIcon" "$INSTDIR\${EXE_NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_EN}" \
    "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_EN}" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_EN}" "NoRepair" 1
  StrCpy $R9 "ok"
FunctionEnd

Function ShowFinish
  Push "bg_finish.bmp"
  Call ShowBackground
  nsDialogs::CreateControl "BUTTON" "${BS_AUTOCHECKBOX}|${WS_CHILD}|${WS_VISIBLE}" 0 0 0 10 10 "اجرای ${APP_NAME}"
  Pop $RunCheck
  Push $RunCheck
  Call TrackCtl
  SetCtlColors $RunCheck "E9EEF1" "0F1418"
  Push $RunCheck
  Push 620
  Push 492
  Push 300
  Push 28
  Call PlaceCtl
  SendMessage $RunCheck 0xF1 1 0
  Push "پایان"
  Call MakeButton
  Pop $BtnNext
  Push $BtnNext
  Push 700
  Push 530
  Push 220
  Push 44
  Call PlaceCtl
  ${NSD_OnClick} $BtnNext OnFinish
FunctionEnd

Function OnFinish
  SendMessage $RunCheck 0xF0 0 0
  Pop $R0
  ${If} $R0 == 1
    ExecShell "" "$INSTDIR\${EXE_NAME}"
  ${EndIf}
  Quit
FunctionEnd

Function OnCancel
  MessageBox MB_YESNO|MB_ICONQUESTION "از نصب ${APP_NAME} انصراف می‌دهید؟" IDYES DoQuit
  Return
  DoQuit:
  Quit
FunctionEnd

; ============================================================
; نقطه‌ی ورود صفحه‌ی custom
; ============================================================
Function ShowMainPage
  Call HideWizardButtons
  Push 960
  Push 600
  Call SetClientSize
  nsDialogs::Create 1018
  Pop $Dialog
  ${If} $Dialog == error
    Abort
  ${EndIf}
  System::Call 'user32::SetWindowPos(i $Dialog, i 0, i 0, i 0, i 960, i 600, i 0x16)'
  StrCpy $Ctl0 0
  StrCpy $Ctl1 0
  StrCpy $Ctl2 0
  StrCpy $Ctl3 0
  StrCpy $Ctl4 0
  StrCpy $Ctl5 0
  StrCpy $Ctl6 0
  StrCpy $Ctl7 0
  StrCpy $Ctl8 0
  StrCpy $Ctl9 0
  StrCpy $BgBmp 0
  Call ShowWelcome
  nsDialogs::Show
FunctionEnd

!ifndef UNINSTALLER_ONLY
Page custom ShowMainPage

; سکشن خالی (نصب واقعی در DoInstall انجام می‌شود)
Section "-hidden"
SectionEnd
!endif

; ============================================================
; حذف کامل (Complete Uninstall) — بعد از اجرا هیچ اثری از برنامه
; روی سیستم نمی‌ماند:
;   ۱) بستن اجباری برنامه‌ی در حال اجرا (وگرنه فایل‌ها قفل می‌مانند)
;   ۲) حذف میان‌برهای منوی استارت و دسکتاپ
;   ۳) حذف کل پوشه‌ی نصب: فایل‌ها + person_data + plate_data +
;      cameras.json + آپدیتر + version.txt + خود uninstall.exe
;   ۴) حذف کلیدهای رجیستری (ورودی Uninstall + تنظیمات)
;   ۵) حذف پوشه‌های داده‌ی خارج از محل نصب (AppData و plate_data کاربر)
;   ۶) حذف فایل‌های موقت
; این بدنه هم در uninstall.exe داخل پوشه‌ی نصب (WriteUninstaller) و هم
; در حذف‌کننده‌ی مستقل (UNINSTALLER_ONLY) استفاده می‌شود.
; ============================================================
!macro FULL_CLEANUP_BODY
  nsExec::ExecToStack '"$SYSDIR\taskkill.exe" /F /IM "${EXE_NAME}"'
  Pop $0
  Pop $1
  Sleep 1000
  Delete "$SMPROGRAMS\${APP_NAME}\*.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_EN}"
  DeleteRegKey HKCU "Software\${APP_EN}"
  DeleteRegKey HKCU "Software\ImenaraSorena"
  RMDir /r "$APPDATA\ImenaraSorena"
  RMDir /r "$LOCALAPPDATA\ImenaraSorena"
  IfFileExists "$PROFILE\plate_data\plates.db" 0 +2
    RMDir /r "$PROFILE\plate_data"
  Delete "$TEMP\${APP_EN}*.*"
!macroend

!ifndef UNINSTALLER_ONLY
Function un.onInit
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "«${APP_NAME}» به‌طور کامل از سیستم حذف شود؟$\n$\nهمه‌ی فایل‌ها، تنظیمات، دوربین‌ها، بانک چهره و سوابق پلاک‌ها برای همیشه پاک می‌شوند." \
    IDYES NoAbort
    Abort
  NoAbort:
FunctionEnd

Section "Uninstall"
  !insertmacro FULL_CLEANUP_BODY
SectionEnd

UninstPage instfiles
!endif

; ============================================================
; حذف‌کننده‌ی مستقل — داخل پکیج setup قرار می‌گیرد تا حتی اگر
; uninstall.exe داخل پوشه‌ی نصب گم شده باشد، حذف کامل ممکن باشد.
; کامپایل: makensis /DVERSION=2.0.0 /DUNINSTALLER_ONLY installer/installer.nsi
; ============================================================
!ifdef UNINSTALLER_ONLY
Function .onInit
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_EN}" "InstallLocation"
  ${If} $0 != ""
    StrCpy $INSTDIR $0
  ${EndIf}
  IfFileExists "$INSTDIR\${EXE_NAME}" FoundInst
    MessageBox MB_YESNO|MB_ICONQUESTION \
      "فایل اصلی برنامه در محل نصب پیدا نشد.$\nمحل بررسی‌شده: $INSTDIR$\n$\nآیا پوشه‌های داده و کلیدهای رجیستری پاک‌سازی شوند؟" \
      IDYES DoClean
    Quit
  FoundInst:
    MessageBox MB_YESNO|MB_ICONQUESTION \
      "«${APP_NAME}» به‌طور کامل از این سیستم حذف شود؟$\n$\nمحل نصب: $INSTDIR$\n$\nهمه‌ی فایل‌ها، تنظیمات، دوربین‌ها، بانک چهره و سوابق پلاک‌ها برای همیشه پاک می‌شوند." \
      IDYES DoClean
    Quit
  DoClean:
    !insertmacro FULL_CLEANUP_BODY
    MessageBox MB_ICONINFORMATION "حذف کامل انجام شد. هیچ اثری از «${APP_NAME}» روی سیستم باقی نماند."
    Quit
FunctionEnd
!endif

; ============================================================
; آماده‌سازی اولیه (فقط حالت نصب)
; ============================================================
!ifndef UNINSTALLER_ONLY
Function .onInit
  InitPluginsDir
  File /oname=$PLUGINSDIR\bg_welcome.bmp "${GFXDIR}\bg_welcome.bmp"
  File /oname=$PLUGINSDIR\bg_dir.bmp "${GFXDIR}\bg_dir.bmp"
  File /oname=$PLUGINSDIR\bg_install.bmp "${GFXDIR}\bg_install.bmp"
  File /oname=$PLUGINSDIR\bg_finish.bmp "${GFXDIR}\bg_finish.bmp"
FunctionEnd
!endif
