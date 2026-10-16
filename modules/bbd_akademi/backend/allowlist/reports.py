"""ÜRETİLEN DOSYA — elle düzenlenmez. Alt alan: reports.

Kaynak: bbd-akademi/docs/api/openapi.yaml (info.version 1.0.0-draft.3).
Üretici: modules/bbd_akademi/tools/gen_allowlist.py
"""

from __future__ import annotations

from ..core.operations import Operation

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        'GET',
        '/v1/admin/early-warning-settings',
        'admin_getEarlyWarningSettings',
        ('insights.settings',),
        'km',
        features=('F-A22',),
    ),
    Operation(
        'PUT',
        '/v1/admin/early-warning-settings',
        'admin_putEarlyWarningSettings',
        ('insights.settings',),
        'km',
        features=('F-A22',),
    ),
    Operation(
        'GET',
        '/v1/admin/early-warnings',
        'admin_listEarlyWarnings',
        ('insights.view',),
        'both',
        feature_flag='early_warning',
        features=('F-A22',),
    ),
    Operation(
        'GET',
        '/v1/admin/early-warnings/{earlyWarningId}',
        'admin_getEarlyWarning',
        ('insights.view',),
        'both',
        feature_flag='early_warning',
        features=('F-A22',),
    ),
    Operation(
        'POST',
        '/v1/admin/early-warnings/{earlyWarningId}/meeting',
        'admin_scheduleEarlyWarningMeeting',
        ('insights.manage',),
        'both',
        feature_flag='early_warning',
        features=('F-A22', 'F-P11'),
    ),
    Operation(
        'POST',
        '/v1/admin/early-warnings/{earlyWarningId}/notes',
        'admin_addEarlyWarningNote',
        ('insights.manage',),
        'both',
        feature_flag='early_warning',
        features=('F-A22', 'F-A21'),
    ),
    Operation(
        'POST',
        '/v1/admin/early-warnings/{earlyWarningId}/notify-guardians',
        'admin_notifyEarlyWarningGuardians',
        ('insights.manage',),
        'both',
        feature_flag='early_warning',
        features=('F-A22',),
    ),
    Operation(
        'POST',
        '/v1/admin/early-warnings/{earlyWarningId}/reopen',
        'admin_reopenEarlyWarning',
        ('insights.manage',),
        'both',
        feature_flag='early_warning',
        features=('F-A22',),
    ),
    Operation(
        'POST',
        '/v1/admin/early-warnings/{earlyWarningId}/resolve',
        'admin_resolveEarlyWarning',
        ('insights.manage',),
        'both',
        feature_flag='early_warning',
        features=('F-A22',),
    ),
    Operation(
        'GET',
        '/v1/admin/exports/{exportId}',
        'admin_getExport',
        ('audit.export', 'exams.export', 'evaluations.export', 'attendance.export', 'coaching.export', 'messages.audit'),
        'km',
        features=('F-A16', 'F-E8', 'F-A21'),
    ),
    Operation(
        'GET',
        '/v1/admin/insights/mistakes',
        'admin_getMistakeInsights',
        ('insights.view',),
        'both',
        features=('F-A22', 'F-S18'),
    ),
    Operation(
        'GET',
        '/v1/admin/insights/topics',
        'admin_getTopicAccuracyInsights',
        ('insights.view',),
        'both',
        features=('F-A22', 'F-S17'),
    ),
)
