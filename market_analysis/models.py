from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.db import models
from django_countries.fields import CountryField
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator, FileExtensionValidator
from django.core.exceptions import ValidationError

DECIMAL_2 = Decimal("0.01")
OVERHEAD_DAYRATE_DEFAULT = Decimal("21000.00")

# Maximum file size for project map images (5 MB)
MAX_PROJECT_MAP_SIZE = 5 * 1024 * 1024


def validate_image_file_size(value):
    """Validate that uploaded file doesn't exceed maximum size."""
    if value.size > MAX_PROJECT_MAP_SIZE:
        raise ValidationError(
            f'File size must be no more than {MAX_PROJECT_MAP_SIZE // (1024 * 1024)} MB. '
            f'Current file size is {value.size / (1024 * 1024):.2f} MB.'
        )


def _serialize_value_for_json(val):
    """Helper to serialize common types for JSON snapshots."""
    if val is None:
        return None
    if isinstance(val, Decimal):
        return str(val)
    if hasattr(val, "isoformat"):  # dates/datetimes
        return val.isoformat()
    # CountryField may provide a country object with .code
    if hasattr(val, "code"):
        return getattr(val, "code", str(val))
    if isinstance(val, models.Model):
        return str(val)
    # Handle ImageFieldFile and FileField values
    if hasattr(val, 'name') and hasattr(val, 'url'):
        return val.name if val else None
    return val


def _build_snapshot_from_instance(inst):
    """Return dict of field-name -> serializable value for a model instance."""
    data = {}
    for f in inst._meta.fields:
        name = f.name
        try:
            data[name] = _serialize_value_for_json(getattr(inst, name))
        except Exception:
            data[name] = None
    return data


class Client(models.Model):
    client_id = models.AutoField(primary_key=True, db_column='ClientID')
    name = models.CharField(max_length=255, db_column='Name')

    class Meta:
        db_table = 'clients'
        verbose_name = 'Client'
        verbose_name_plural = 'Clients'

    def __str__(self):
        return self.name


class Project(models.Model):
    BID_TYPE = [
        ('RFQ', 'Request for Quotation'),
        ('RFP', 'Request for Proposal'),
        ('RFI', 'Request for Information'),
        ('MC', 'Multi-Client'),
        ('DR', 'Direct Award'),
        ('BQ', 'Budgetary Quotation')
    ]

    REGIONS = [
        ('NSA', 'NSA'),
        ('AMME', 'AMME'),
        ('Asia', 'Asia'),
        ('Australasia', 'Australasia'),
        ('Europe', 'Europe'),
        ('Global', 'Global'),
    ]

    STATUS = [
        ('Ongoing', 'Ongoing'),
        ('Submitted', 'Submitted'),
        ('Won', 'Won'),
        ('Lost', 'Lost'),
        ('Cancelled', 'Cancelled'),
        ('No Bid', 'No Bid')
    ]

    STATUS_CODES = {
        'Ongoing': 'ONG',
        'Submitted': 'SUB',
        'Won': 'WON',
        'Lost': 'LST',
        'Cancelled': 'CXL',
        'No Bid': 'NBD',
    }

    project_id = models.AutoField(primary_key=True, db_column='ProjectID')
    internal_id = models.CharField(max_length=200, db_column='InternalID', blank=True)
    bid_type = models.CharField(max_length=10, choices=BID_TYPE, db_column='BidType', default='BQ')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, db_column='ClientID', related_name='projects', null=True, blank=True)
    name = models.CharField(max_length=255, db_column='Name')
    country = CountryField(db_column='Country')
    region = models.CharField(max_length=12, choices=REGIONS, db_column='Region', null=True, blank=True)
    date_received = models.DateField(null=True, blank=True, db_column='DateReceived')

    # New status/date fields
    status = models.CharField(max_length=10, choices=STATUS, db_column='Status', default='Ongoing')
    submission_date = models.DateField(null=True, blank=True, db_column='SubmissionDate')
    award_date = models.DateField(null=True, blank=True, db_column='AwardDate')
    lost_date = models.DateField(null=True, blank=True, db_column='LostDate')

    project_portal_id = models.CharField(max_length=100, null=True, blank=True, db_column='ProjectPortalID')
    
    # Dashboard display fields
    deadline_date = models.DateField(null=True, blank=True, db_column='DeadlineDate')
    comments = models.TextField(null=True, blank=True, db_column='Comments')
    
    # Project map image field
    project_map = models.ImageField(
        upload_to='project_maps/',
        null=True,
        blank=True,
        db_column='ProjectMap',
        validators=[
            FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'gif']),
            validate_image_file_size
        ],
        help_text='Upload a project map image (PNG, JPG, GIF). Max size: 5 MB.'
    )

    def save(self, *args, **kwargs):
        """
        Detect status and bid_type transitions:
        - Set submission/award/lost dates on relevant transitions (before saving so they persist)
        - Create a ProjectSnapshot (JSON) of the previous state when bid_type or status changes.
        - Update internal_id to reflect new status (does not create new Project rows).
        - After saving, create BidTypeHistory, ProjectStatusHistory and ChangeLog entries.
        """
        # fetch previous values if object exists
        prev = None
        prev_bid = None
        prev_status = None
        if self.pk:
            try:
                prev = Project.objects.get(pk=self.pk)
                prev_bid = prev.bid_type
                prev_status = prev.status
            except Project.DoesNotExist:
                prev = None
                prev_bid = None
                prev_status = None

        # set date fields for known transitions BEFORE saving so they persist in the same save
        today = timezone.now().date()

        # Ongoing -> Submitted (or creation already in Submitted) -> set submission_date if missing
        if self.status == 'Submitted' and (prev_status is None or prev_status == 'Ongoing' or prev_status != 'Submitted'):
            if not self.submission_date:
                self.submission_date = today

        # Submitted -> Won -> set award_date if missing
        if prev_status == 'Submitted' and self.status == 'Won':
            if not self.award_date:
                self.award_date = today

        # Submitted -> Lost -> set lost_date if missing
        if prev_status == 'Submitted' and self.status == 'Lost':
            if not self.lost_date:
                self.lost_date = today

        # If bid_type or status will change, create a ProjectSnapshot (of previous state)
        try:
            if prev is not None:
                if prev_bid is not None and prev_bid != self.bid_type:
                    ProjectSnapshot.objects.create(
                        project=self,
                        change_type='BID',
                        snapshot=_build_snapshot_from_instance(prev),
                        snapshot_name=(prev.internal_id or prev.name),
                    )
                if prev_status is not None and prev_status != self.status:
                    ProjectSnapshot.objects.create(
                        project=self,
                        change_type='STATUS',
                        snapshot=_build_snapshot_from_instance(prev),
                        snapshot_name=(prev.internal_id or prev.name),
                    )
        except Exception:
            # don't break the save flow on snapshot errors
            pass

        # Update internal_id to include STATUS code on status change (build before saving)
        if prev_status is not None and prev_status != self.status:
            try:
                # build the base internal_id from current fields (date_received, bid_type, client, name, country)
                ym = self.date_received.strftime("%Y%m") if self.date_received else ""
                bid = (self.bid_type or "").upper()
                client_name = ""
                if self.client:
                    try:
                        client_name = (self.client.name or "").replace(" ", "").upper()
                    except Exception:
                        client_name = ""
                proj3 = (self.name or "")[:3].upper()
                country_code = getattr(self.country, "code", None) or (str(self.country) if self.country else "")

                def sanitize(s: str) -> str:
                    return "".join(ch for ch in (s or "") if ch.isalnum())

                parts = [sanitize(ym), sanitize(bid), sanitize(client_name), sanitize(proj3), sanitize(country_code)]
                status_code = self.STATUS_CODES.get(self.status, "".join(ch for ch in (self.status or "").upper()[:3] if ch.isalnum()))
                if status_code:
                    parts.append(sanitize(status_code))
                self.internal_id = "-".join(part for part in parts if part)
            except Exception:
                # on failure leave internal_id unchanged
                pass

        super().save(*args, **kwargs)

        # Create bid type history if changed
        if prev_bid != self.bid_type:
            try:
                BidTypeHistory.objects.create(
                    project=self,
                    previous_bid_type=prev_bid,
                    new_bid_type=self.bid_type
                )
            except Exception:
                pass

        # Create project status history and ensure contract object for wins
        if prev_status != self.status:
            try:
                ProjectStatusHistory.objects.create(
                    project=self,
                    previous_status=prev_status,
                    new_status=self.status
                )
            except Exception:
                pass

            # if project has become Won, ensure a ProjectContract row exists
            if self.status == 'Won':
                try:
                    ProjectContract.objects.get_or_create(project=self)
                except Exception:
                    pass

        # Create unified ChangeLog entries (no changed_by here — set in views/admin when available)
        try:
            # status change
            if prev_status != self.status:
                ChangeLog.objects.create(
                    project=self,
                    change_type='STATUS',
                    field_name='status',
                    previous_value=prev_status,
                    new_value=self.status,
                    event_date=(self.submission_date if self.status == 'Submitted' else
                                self.award_date if self.status == 'Won' else
                                self.lost_date if self.status == 'Lost' else None),
                )
            # bid_type change
            if prev_bid != self.bid_type:
                ChangeLog.objects.create(
                    project=self,
                    change_type='BID',
                    field_name='bid_type',
                    previous_value=prev_bid,
                    new_value=self.bid_type,
                )
        except Exception:
            pass

    class Meta:
        db_table = 'projects'
        verbose_name = 'Project'
        verbose_name_plural = 'Projects'

    def __str__(self):
        return self.name


# Build internal_id before saving a Project ------------------------------------------------
@receiver(pre_save, sender=Project)
def build_internal_id(sender, instance: Project, **kwargs):
    """
    Populate `internal_id` if blank and `date_received` is present.
    Format: YYYYMM-BID-CLIENTNAME-PROJ3-COUNTRY
    - CLIENTNAME: no spaces, uppercase
    - PROJ3: first 3 characters of project name, uppercase
    - COUNTRY: ISO code (falls back to string value if necessary)
    """
    if instance.internal_id:
        return

    if not instance.date_received:
        return

    try:
        ym = instance.date_received.strftime("%Y%m")
    except Exception:
        return

    bid = (instance.bid_type or "").upper()

    client_name = ""
    try:
        if instance.client:
            client_name = (instance.client.name or "").replace(" ", "").upper()
    except Exception:
        client_name = ""

    proj3 = (instance.name or "")[:3].upper()

    country_code = ""
    try:
        # CountryField typically provides a country object with .code
        country_code = getattr(instance.country, "code", None) or (str(instance.country) if instance.country else "")
    except Exception:
        country_code = ""

    # remove any characters that could make the identifier unsafe (keep alphanumerics)
    def sanitize(s: str) -> str:
        return "".join(ch for ch in (s or "") if ch.isalnum())

    internal_parts = [sanitize(ym), sanitize(bid), sanitize(client_name), sanitize(proj3), sanitize(country_code)]
    # join with hyphens, drop empty parts
    internal_id = "-".join(part for part in internal_parts if part)
    instance.internal_id = internal_id
# -----------------------------------------------------------------------------------------


class BidTypeHistory(models.Model):
    """
    Stores the timeline of bid_type values for a Project.
    Each row represents a transition (previous -> new) with a timestamp.
    """
    id = models.AutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column='ProjectID', related_name='bid_history')
    previous_bid_type = models.CharField(max_length=10, choices=Project.BID_TYPE, null=True, blank=True)
    new_bid_type = models.CharField(max_length=10, choices=Project.BID_TYPE)
    changed_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'bid_type_history'
        verbose_name = 'Bid Type History'
        verbose_name_plural = 'Bid Type Histories'
        ordering = ['-changed_at']

    def __str__(self):
        prev = self.previous_bid_type or "None"
        return f"{self.project.name}: {prev} -> {self.new_bid_type} at {self.changed_at}"


class ProjectStatusHistory(models.Model):
    """
    Stores status change history for a Project and timestamp.
    """
    id = models.AutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column='ProjectID', related_name='status_history')
    previous_status = models.CharField(max_length=20, null=True, blank=True)
    new_status = models.CharField(max_length=20)
    changed_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'project_status_history'
        verbose_name = 'Project Status History'
        verbose_name_plural = 'Project Status Histories'
        ordering = ['-changed_at']

    def __str__(self):
        prev = self.previous_status or "None"
        return f"{self.project.name}: {prev} -> {self.new_status} at {self.changed_at}"


class ProjectTechnology(models.Model):
    SURVEY_TYPES = [
        ('2D Seismic', '2D Seismic'),
        ('3D Seismic', '3D Seismic'),
        ('4D Seismic', '4D Seismic'),
        ('GM', 'Gravity & Magnetics'),
        ('NES', 'New Energy Solutions'),
        ('HYBD', 'Hybrid')
    ]

    TECHNOLOGY = [
        ('STR', 'Streamer'),
        ('OBN', 'OBN'),
        ('OTHER', 'Other')
    ]

    OBN_TECHNIQUE = [
        ('NOAR', 'NOAR'),
        ('ROV', 'ROV'),
        ('DN', 'DN')
    ]

    OBN_SYSTEM = [
        ('ZXPLR', 'ZXPLR'),
        ('Z700', 'Z700'),
        ('MASS', 'MASS'),
        ('GPR300', 'GPR300'),
        ('OTHER', 'Other')
    ]

    STREAMER = [
        ('CONV', 'Conventional'),
        ('DUAL', 'Dual Sensor'),
        ('GSTRM', 'GeoStreamer'),
    ]

    id = models.AutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column='ProjectID', related_name='technologies')
    technology = models.CharField(max_length=50, choices=TECHNOLOGY, db_column='Technologies')
    survey_type = models.CharField(max_length=50, choices=SURVEY_TYPES, db_column='SurveyType')
    obn_technique = models.CharField(max_length=50, choices=OBN_TECHNIQUE, null=True, blank=True, db_column='OBNTechnique')
    obn_system = models.CharField(max_length=50, choices=OBN_SYSTEM, null=True, blank=True, db_column='OBNSystem')
    streamer = models.CharField(max_length=50, choices=STREAMER, null=True, blank=True, db_column='Streamer')

    class Meta:
        db_table = 'project_technologies'
        verbose_name = 'Project Technology'
        verbose_name_plural = 'Project Technologies'

    def __str__(self):
        return f"{self.technology} for Project {self.project.name}"


class ProjectContract(models.Model):
    """
    One-to-one contract details for projects that are Won.
    Created automatically when a Project transitions to 'Won' (if missing).
    Admin/UI should surface this inline only for Won projects.
    """
    id = models.AutoField(primary_key=True)
    project = models.OneToOneField(Project, on_delete=models.CASCADE, db_column='ProjectID', related_name='contract')
    contract_date = models.DateField(null=True, blank=True, db_column='ContractDate')
    actual_start = models.DateField(null=True, blank=True, db_column='ActualStart')
    actual_end = models.DateField(null=True, blank=True, db_column='ActualEnd')
    actual_duration = models.IntegerField(null=True, blank=True, db_column='ActualDuration', help_text='Duration (days) derived from start/end')

    class Meta:
        db_table = 'project_contracts'
        verbose_name = 'Project Contract'
        verbose_name_plural = 'Project Contracts'

    def __str__(self):
        return f"Contract for {self.project.name}"

    def save(self, *args, **kwargs):
        # compute actual_duration if start and end provided
        if self.actual_start and self.actual_end:
            try:
                delta = self.actual_end - self.actual_start
                self.actual_duration = max(0, int(delta.days))
            except Exception:
                self.actual_duration = None
        super().save(*args, **kwargs)


class ProjectSnapshot(models.Model):
    """
    Snapshot of Project state BEFORE a change. Kept related to the same Project.
    """
    CHANGE_TYPES = [
        ('BID', 'Bid type change'),
        ('STATUS', 'Status change'),
        ('OTHER', 'Other'),
    ]

    id = models.AutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column='ProjectID', related_name='snapshots')
    change_type = models.CharField(max_length=16, choices=CHANGE_TYPES)
    snapshot = models.JSONField()
    snapshot_name = models.CharField(max_length=200, null=True, blank=True, help_text='Previous internal_id or name')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'project_snapshots'
        verbose_name = 'Project Snapshot'
        verbose_name_plural = 'Project Snapshots'
        ordering = ['-created_at']

    def __str__(self):
        return f"Snapshot for {self.project.name} ({self.change_type}) at {self.created_at}"


class ChangeLog(models.Model):
    """
    Unified change log for status and bid_type (and future event types).
    Keep as authoritative single timeline. Backfill existing history into this.
    """
    CHANGE_TYPES = [
        ('STATUS', 'Status change'),
        ('BID', 'Bid type change'),
        ('OTHER', 'Other'),
    ]

    id = models.AutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column='ProjectID', related_name='changelog')
    change_type = models.CharField(max_length=16, choices=CHANGE_TYPES)
    field_name = models.CharField(max_length=50)  # e.g. "status" or "bid_type"
    previous_value = models.CharField(max_length=200, null=True, blank=True)
    new_value = models.CharField(max_length=200, null=True, blank=True)
    event_date = models.DateField(null=True, blank=True)  # submission/award/lost when relevant
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'change_log'
        ordering = ['-changed_at']
        verbose_name = 'Change Log'
        verbose_name_plural = 'Change Logs'

    def __str__(self):
        return f"{self.project.name}: {self.change_type} {self.previous_value} -> {self.new_value} at {self.changed_at}"


class Financial(models.Model):
    """
    Financials for a project. Several fields are derived from inputs and are
    automatically calculated on save.
    """
    id = models.AutoField(primary_key=True)
    project = models.OneToOneField(Project, on_delete=models.CASCADE, db_column='ProjectID', related_name='financials')

    # User inputs
    total_direct_cost = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='Cost')
    # gm entered as percent (e.g. 20.00 == 20%)
    gm = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, db_column='GM', help_text='Gross Margin %')
    # overhead dayrate
    overhead_dayrate = models.DecimalField(max_digits=15, decimal_places=2, null=False, blank=True, db_column='OVERHEAD_DAYRATE', default=OVERHEAD_DAYRATE_DEFAULT)

    duration_raw = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, db_column='Duration', help_text='Duration in days')
    # valid python attribute name, map to same DB column used previously
    duration_with_dt = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, db_column='Duration_w_dt', help_text='Duration with downtime in days')

    depreciation = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='Depreciation')

    # Derived fields (will be populated on save)
    total_revenue = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='TotalRevenue')
    gp = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='GP', help_text='Gross Profit $')
    total_overhead = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='TotalOverhead')

    ebitda_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='EBITDA_USD', help_text='EBITDA in $')
    ebitda_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, db_column='EBITDA_PCT', help_text='EBITDA in %')

    ebit_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='EBIT', help_text='EBIT in $')
    ebit_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, db_column='EBIT_PCT', help_text='EBIT in %')

    taxes = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='Taxes')
    net_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='NET_USD', help_text='Net Income $')
    net_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, db_column='NET_PCT', help_text='Net Income %')

    ebit_day = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='EBIT_PER_DAY')
    net_day = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, db_column='NET_PER_DAY')

    file_upload_TMA = models.FileField(upload_to='financial_uploads/', null=True, blank=True, db_column='FileUpload_TMA')

    class Meta:
        db_table = 'financials'
        verbose_name = 'Financial'
        verbose_name_plural = 'Financials'

    def __str__(self):
        return f"Financials for Project {self.project.name}"

    def _to_decimal(self, value):
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(value)
        except (InvalidOperation, TypeError, ValueError):
            return None

    def _quantize_money(self, value):
        if value is None:
            return None
        return value.quantize(DECIMAL_2, rounding=ROUND_HALF_UP)

    def _quantize_pct(self, value):
        if value is None:
            return None
        return value.quantize(DECIMAL_2, rounding=ROUND_HALF_UP)

    def save(self, *args, **kwargs):
        """
        Populate derived financial fields:
        - gm is treated as percent and converted to fraction (gm_frac = gm / 100)
        - total_revenue = total_direct_cost / (1 - gm_frac)
        - gp = total_revenue - total_direct_cost
        - total_overhead = overhead_dayrate * duration_with_dt
        - ebitda_amount = gp - total_overhead
        - ebitda_pct = (ebitda_amount / total_revenue) * 100
        - ebit_amount = ebitda_amount - depreciation
        - ebit_pct = (ebit_amount / total_revenue) * 100
        - net_amount = ebit_amount - taxes
        - net_pct = (net_amount / total_revenue) * 100
        - ebit_day = ebit_amount / duration_with_dt
        - net_day = net_amount / duration_with_dt
        """
        cost = self._to_decimal(self.total_direct_cost)
        gm_pct = self._to_decimal(self.gm)  # expected as percent, e.g., 20.00
        overhead_rate = self._to_decimal(self.overhead_dayrate) or OVERHEAD_DAYRATE_DEFAULT
        depreciation = self._to_decimal(self.depreciation)
        taxes = self._to_decimal(self.taxes)

        duration_td = self._to_decimal(self.duration_with_dt)

        # compute gm fraction
        gm_frac = None
        if gm_pct is not None:
            try:
                gm_frac = (gm_pct / Decimal("100"))
            except (InvalidOperation, ZeroDivisionError):
                gm_frac = None

        # total_revenue = cost / (1 - gm_frac)
        total_revenue = None
        if cost is not None and gm_frac is not None:
            try:
                denom = (Decimal("1") - gm_frac)
                if denom != 0:
                    total_revenue = cost / denom
            except (InvalidOperation, ZeroDivisionError):
                total_revenue = None

        # gp = total_revenue - cost
        gp = None
        if total_revenue is not None and cost is not None:
            gp = total_revenue - cost

        # total_overhead = overhead_rate * duration_td
        total_overhead = None
        if overhead_rate is not None and duration_td is not None and duration_td != 0:
            try:
                total_overhead = overhead_rate * duration_td
            except (InvalidOperation, TypeError):
                total_overhead = None

        # ebitda_amount = gp - total_overhead
        ebitda_amount = None
        if gp is not None:
            if total_overhead is not None:
                ebitda_amount = gp - total_overhead
            else:
                ebitda_amount = gp

        # ebitda_pct = (ebitda_amount / total_revenue) * 100
        ebitda_pct = None
        if ebitda_amount is not None and total_revenue is not None and total_revenue != 0:
            try:
                ebitda_pct = (ebitda_amount / total_revenue) * Decimal("100")
            except (InvalidOperation, ZeroDivisionError):
                ebitda_pct = None

        # ebit_amount = ebitda_amount - depreciation
        ebit_amount = None
        if ebitda_amount is not None:
            if depreciation is not None:
                ebit_amount = ebitda_amount - depreciation
            else:
                ebit_amount = ebitda_amount

        # ebit_pct = (ebit_amount / total_revenue) * 100
        ebit_pct = None
        if ebit_amount is not None and total_revenue is not None and total_revenue != 0:
            try:
                ebit_pct = (ebit_amount / total_revenue) * Decimal("100")
            except (InvalidOperation, ZeroDivisionError):
                ebit_pct = None

        # net_amount = ebit_amount - taxes
        net_amount = None
        if ebit_amount is not None:
            if taxes is not None:
                net_amount = ebit_amount - taxes
            else:
                net_amount = ebit_amount

        # net_pct = (net_amount / total_revenue) * 100
        net_pct = None
        if net_amount is not None and total_revenue is not None and total_revenue != 0:
            try:
                net_pct = (net_amount / total_revenue) * Decimal("100")
            except (InvalidOperation, ZeroDivisionError):
                net_pct = None

        # ebit_day and net_day (divide by duration_td)
        ebit_day = None
        net_day = None
        if duration_td is not None and duration_td > 0:
            if ebit_amount is not None:
                try:
                    ebit_day = ebit_amount / duration_td
                except (InvalidOperation, ZeroDivisionError):
                    ebit_day = None
            if net_amount is not None:
                try:
                    net_day = net_amount / duration_td
                except (InvalidOperation, ZeroDivisionError):
                    net_day = None

        # Quantize/round monetary and percent fields
        self.total_revenue = self._quantize_money(total_revenue)
        self.gp = self._quantize_money(gp)
        self.total_overhead = self._quantize_money(total_overhead)
        self.ebitda_amount = self._quantize_money(ebitda_amount)
        self.ebitda_pct = self._quantize_pct(ebitda_pct)
        self.ebit_amount = self._quantize_money(ebit_amount)
        self.ebit_pct = self._quantize_pct(ebit_pct)
        self.net_amount = self._quantize_money(net_amount)
        self.net_pct = self._quantize_pct(net_pct)
        self.ebit_day = self._quantize_money(ebit_day)
        self.net_day = self._quantize_money(net_day)

        super().save(*args, **kwargs)


class Competitor(models.Model):
    """
    Competitor that won a bid when a Project is marked as Lost.
    Stored related to the same Project (not creating new Project rows).
    Fixed list of competitor names (choices).
    Allow blank/null if the winning competitor is unknown.
    """
    COMPETITOR_CHOICES = [
        ('SAE', 'SAE'),
        ('PXGEO', 'PXGEO'),
        ('VIRIDIEN', 'Viridien'),
        ('SLB', 'SLB'),
        ('SHEARWATER', 'Shearwater'),
        ('BGP', 'BGP'),
        ('COSL', 'COSL'),
    ]

    id = models.AutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column='ProjectID', related_name='competitors')
    name = models.CharField(max_length=50, choices=COMPETITOR_CHOICES, db_column='Name', null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'competitors'
        verbose_name = 'Competitor'
        verbose_name_plural = 'Competitors'
        ordering = ['-created_at']

    def __str__(self):
        # show human readable label for choice or fallback to "Unknown"
        label = self.get_name_display() if self.name else "Unknown"
        return f"{label} ({self.project.internal_id or self.project.name})"


class ScopeOfWork(models.Model):
    """
    Scope of Work entries for a Project.
    Each row represents a distinct scope item.
    Geophysical Parameters
    """
    NODE_CATEGORY = [ 
        ("Shallow Water", "Shallow Water"),
        ("Deep Water", "Deep Water")]

    id = models.AutoField(primary_key=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column='ProjectID', related_name='scopes_of_work')
    total_rx_locs = models.IntegerField(null=True, blank=True, db_column='TotalRxLocs')
    total_sx_locs = models.IntegerField(null=True, blank=True, db_column='TotalSxLocs')
    max_active_spread = models.IntegerField(null=True, blank=True, db_column='MaxActiveSpread')
    crew_node_count = models.IntegerField(null=True, blank=True, db_column='CrewNodeCount')
    node_area = models.IntegerField(null=True, blank=True, db_column='NodeArea')
    source_area = models.IntegerField(null=True, blank=True, db_column='SourceArea')
    node_grid_IL = models.IntegerField(null=True, blank=True, db_column='NodeGridIL')
    node_grid_XL = models.IntegerField(null=True, blank=True, db_column='NodeGridXL')
    source_grid_IL = models.IntegerField(null=True, blank=True, db_column='SourceGridIL')
    source_grid_XL = models.IntegerField(null=True, blank=True, db_column='SourceGridXL')
    water_depth_min = models.IntegerField(null=True, blank=True, db_column='WaterDepth')
    water_depth_max = models.IntegerField(null=True, blank=True, db_column='WaterDepthMax')
    node_category = models.CharField(max_length=20, choices=NODE_CATEGORY, null=True, blank=True, db_column='NodeCategory')


    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = 'scope_of_work'
        verbose_name = 'Scope of Work'
        verbose_name_plural = 'Scopes of Work'
        ordering = ['-created_at']

    def __str__(self):
        return f"Scope of Work for {self.project.name} created at {self.created_at}"
