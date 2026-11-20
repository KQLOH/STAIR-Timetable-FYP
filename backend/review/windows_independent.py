"""Native Windows password reauthentication; no browser password or impersonation.

CredUI DO_NOT_PERSIST + LogonUser(INTERACTIVE). The token is used only to read
its real SID, then closed. DPAPI always runs as the original host owner.
See Microsoft wincred CredUIPromptForCredentialsW and winbase LogonUserW.
"""
import ctypes
from ctypes import wintypes
import os


class CredUIInfo(ctypes.Structure):
    _fields_=[('cbSize',wintypes.DWORD),('hwndParent',wintypes.HWND),('pszMessageText',wintypes.LPCWSTR),('pszCaptionText',wintypes.LPCWSTR),('hbmBanner',wintypes.HANDLE)]


def token_actor(token,kernel,security):
    security.GetTokenInformation.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    security.GetTokenInformation.restype=wintypes.BOOL
    security.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(wintypes.LPWSTR)]
    security.ConvertSidToStringSidW.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    size=wintypes.DWORD();security.GetTokenInformation(token,1,None,0,ctypes.byref(size))
    if not 0<size.value<=65536:raise RuntimeError('REVIEWER_WINDOWS_IDENTITY_UNAVAILABLE')
    data=ctypes.create_string_buffer(size.value)
    if not security.GetTokenInformation(token,1,data,size,ctypes.byref(size)):raise RuntimeError('REVIEWER_WINDOWS_IDENTITY_UNAVAILABLE')
    sid=ctypes.cast(data,ctypes.POINTER(ctypes.c_void_p))[0];text=wintypes.LPWSTR()
    if not security.ConvertSidToStringSidW(sid,ctypes.byref(text)):raise RuntimeError('REVIEWER_WINDOWS_IDENTITY_UNAVAILABLE')
    try:
        value=text.value
        if value in {'S-1-5-7','S-1-5-18','S-1-5-19','S-1-5-20'} or value.endswith('-501'):raise RuntimeError('INDIVIDUAL_WINDOWS_ACCOUNT_REQUIRED')
        return 'windows-logon:'+value
    finally:kernel.LocalFree(ctypes.cast(text,ctypes.c_void_p))


def authenticate_independent(*,purpose="sign-in",context_label=""):
    if purpose not in {"sign-in","verification"}:raise RuntimeError("REVIEWER_AUTHENTICATION_PURPOSE_INVALID")
    if os.name!='nt':raise RuntimeError('WINDOWS_REVIEWER_AUTHENTICATION_REQUIRED')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);security=ctypes.WinDLL('advapi32',use_last_error=True);ui=ctypes.WinDLL('credui',use_last_error=True)
    kernel.GetConsoleWindow.restype=wintypes.HWND;kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    ui.CredUIPromptForCredentialsW.argtypes=[ctypes.POINTER(CredUIInfo),wintypes.LPCWSTR,ctypes.c_void_p,wintypes.DWORD,wintypes.LPWSTR,wintypes.ULONG,wintypes.LPWSTR,wintypes.ULONG,ctypes.POINTER(wintypes.BOOL),wintypes.DWORD]
    ui.CredUIPromptForCredentialsW.restype=wintypes.DWORD
    security.LogonUserW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE)]
    security.LogonUserW.restype=wintypes.BOOL
    user=ctypes.create_unicode_buffer(514);password=ctypes.create_unicode_buffer(257);save=wintypes.BOOL(False);token=wintypes.HANDLE()
    message=('Confirm your OWN submitted whole-timetable check and temporal scope. This records independent verification only if validation passes; it never approves calendar writes.' if purpose=='verification' else 'Enter your OWN separate Windows account password. This signs in only to the shared timetable check; it does not verify any classes.')
    info=CredUIInfo(ctypes.sizeof(CredUIInfo),kernel.GetConsoleWindow(),message+' '+context_label,'STAIR confirm independent check' if purpose=='verification' else 'STAIR independent reviewer sign-in',None)
    try:
        # DO_NOT_PERSIST | ALWAYS_SHOW_UI | GENERIC_CREDENTIALS | EXCLUDE_CERTIFICATES
        status=ui.CredUIPromptForCredentialsW(ctypes.byref(info),'STAIR local independent review',None,0,user,len(user),password,len(password),ctypes.byref(save),0x2|0x80|0x40000|0x8)
        if status==1223:raise RuntimeError('REVIEWER_SIGN_IN_CANCELLED')
        if status or save.value or not password[0]:raise RuntimeError('REVIEWER_SIGN_IN_FAILED')
        account=user.value
        if '\\' in account:domain,account=account.split('\\',1)
        else:domain=None if '@' in account else '.'
        if not security.LogonUserW(account,domain,password,2,0,ctypes.byref(token)):raise RuntimeError('REVIEWER_SIGN_IN_FAILED')
        return token_actor(token,kernel,security)
    finally:
        if token:kernel.CloseHandle(token)
        ctypes.memset(ctypes.addressof(password),0,ctypes.sizeof(password))
        ctypes.memset(ctypes.addressof(user),0,ctypes.sizeof(user))
