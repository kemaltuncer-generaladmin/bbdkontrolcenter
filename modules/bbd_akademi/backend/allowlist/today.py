"""ÜRETİLEN DOSYA — elle düzenlenmez. Alt alan: today.

Kaynak: bbd-akademi/docs/api/openapi.yaml (info.version 1.0.0-draft.3).
Üretici: modules/bbd_akademi/tools/gen_allowlist.py
"""

from __future__ import annotations

from ..core.operations import Operation

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        'GET',
        '/v1/admin/dashboard',
        'admin_getDashboard',
        ('dashboard.view',),
        'both',
        features=('F-A1',),
    ),
    Operation(
        'GET',
        '/v1/admin/etut-requests',
        'admin_listEtutRequests',
        ('etut.manage',),
        'both',
        features=('F-A14', 'F-A6', 'F-S13'),
    ),
    Operation(
        'POST',
        '/v1/admin/etut-requests/{etutRequestId}/decline',
        'admin_declineEtut',
        ('etut.manage',),
        'both',
        features=('F-A14', 'F-S13'),
    ),
    Operation(
        'POST',
        '/v1/admin/etut-requests/{etutRequestId}/schedule',
        'admin_scheduleEtut',
        ('etut.manage',),
        'both',
        features=('F-A14', 'F-A6', 'F-S13'),
    ),
    Operation(
        'GET',
        '/v1/admin/leave-requests',
        'admin_listLeaveRequests',
        ('leave.manage',),
        'both',
        features=('F-A14', 'F-P10'),
    ),
    Operation(
        'POST',
        '/v1/admin/leave-requests/{leaveRequestId}/approve',
        'admin_approveLeaveRequest',
        ('leave.manage',),
        'both',
        features=('F-A14', 'F-P10', 'F-A7', 'F-Y11'),
    ),
    Operation(
        'POST',
        '/v1/admin/leave-requests/{leaveRequestId}/decline',
        'admin_declineLeaveRequest',
        ('leave.manage',),
        'both',
        features=('F-A14', 'F-P10', 'F-Y11'),
    ),
    Operation(
        'GET',
        '/v1/admin/meeting-requests',
        'admin_listMeetingRequests',
        ('meetings.manage',),
        'both',
        features=('F-A14', 'F-P11'),
    ),
    Operation(
        'POST',
        '/v1/admin/meeting-requests/{meetingRequestId}/complete',
        'admin_completeMeeting',
        ('meetings.manage',),
        'both',
        features=('F-A14', 'F-P11'),
    ),
    Operation(
        'POST',
        '/v1/admin/meeting-requests/{meetingRequestId}/decline',
        'admin_declineMeeting',
        ('meetings.manage',),
        'both',
        features=('F-A14', 'F-P11'),
    ),
    Operation(
        'POST',
        '/v1/admin/meeting-requests/{meetingRequestId}/schedule',
        'admin_scheduleMeeting',
        ('meetings.manage',),
        'both',
        features=('F-A14', 'F-P11'),
    ),
)
