from django import forms
from django.forms import DateInput
from django.forms.models import inlineformset_factory
from .models import Project, ProjectTechnology, Client, Financial, Competitor, ProjectContract, ProjectSnapshot

class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            'bid_type', 'client', 'name', 'country', 'region',
            'date_received', 'status', 'submission_date'
        ]
        widgets = {
            'date_received': DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'submission_date': DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }
        labels = {
            'name': 'Project Name',
            'bid_type': 'Bid Type',
            'date_received': 'Date Received',
            'submission_date': 'Submission Date',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Styling classes
        for name, field in self.fields.items():
            if not isinstance(field.widget, (DateInput,)):
                field.widget.attrs.setdefault('class', 'form-control')

        # client as select
        if 'client' in self.fields:
            self.fields['client'].widget.attrs.setdefault('class', 'form-select')

        # submission_date optional by default
        self.fields['submission_date'].required = False

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get('status')
        submission_date = cleaned.get('submission_date')

        if status == 'Submitted' and not submission_date:
            self.add_error('submission_date', 'Submission date is required when status is Submitted.')

        return cleaned


class ProjectTechnologyForm(forms.ModelForm):
    class Meta:
        model = ProjectTechnology
        fields = ('survey_type', 'technology', 'obn_technique', 'obn_system', 'streamer')
        widgets = {
            'survey_type': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'technology': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'obn_technique': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'obn_system': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'streamer': forms.Select(attrs={'class': 'form-select form-select-sm'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['obn_technique'].required = False
        self.fields['obn_system'].required = False
        self.fields['streamer'].required = False


ProjectTechnologyFormSet = inlineformset_factory(
    parent_model=Project,
    model=ProjectTechnology,
    form=ProjectTechnologyForm,
    fields=('survey_type', 'technology', 'obn_technique', 'obn_system', 'streamer'),
    extra=1,
    can_delete=True
)


class FinancialForm(forms.ModelForm):
    class Meta:
        model = Financial
        fields = (
            'total_direct_cost',
            'gm',
            'overhead_dayrate',
            'duration_with_dt',
            'depreciation',
            'taxes',
            'file_upload_TMA',
        )
        widgets = {
            'total_direct_cost': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.01', 'min': '0'}),
            'gm': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.01', 'min': '0', 'max': '100'}),
            'overhead_dayrate': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.01', 'min': '0'}),
            'duration_with_dt': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'min': '0'}),
            'depreciation': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.01', 'min': '0'}),
            'taxes': forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'step': '0.01', 'min': '0'}),
            'file_upload_TMA': forms.ClearableFileInput(attrs={'class': 'form-control form-control-sm'}),
        }
        labels = {
            'total_direct_cost': 'Total Direct Cost (USD)',
            'gm': 'Gross Margin (%)',
            'overhead_dayrate': 'Overhead Dayrate (USD)',
            'duration_with_dt': 'Duration (days, incl. downtime)',
            'depreciation': 'Depreciation (USD)',
            'taxes': 'Taxes (USD)',
            'file_upload_TMA': 'Attachment (optional)',
        }

    def clean_gm(self):
        gm = self.cleaned_data.get('gm')
        if gm is None:
            return gm
        if gm < 0 or gm > 100:
            raise forms.ValidationError('GM must be between 0 and 100.')
        return gm

    def clean_duration_with_dt(self):
        d = self.cleaned_data.get('duration_with_dt')
        if d is None:
            return d
        if d < 0:
            raise forms.ValidationError('Duration must be zero or positive.')
        return d


class ProjectEditForm(forms.ModelForm):
    # include an explicit blank option so the user can pick "Unknown"
    COMPETITOR_SELECT_CHOICES = [('', 'Unknown / Not specified')] + list(getattr(Competitor, 'COMPETITOR_CHOICES', []))
    competitor_name = forms.ChoiceField(
        choices=COMPETITOR_SELECT_CHOICES,
        required=False,
        label='Competitor (if Lost)',
        widget=forms.Select(attrs={'class': 'form-select form-select-sm'})
    )

    class Meta:
        model = Project
        fields = [
            'bid_type', 'client', 'name', 'country', 'region',
            'date_received', 'status', 'submission_date', 'award_date', 'lost_date'
        ]
        widgets = {
            'date_received': DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'submission_date': DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'award_date': DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'lost_date': DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
        }
        labels = {
            'name': 'Project Name',
            'bid_type': 'Bid Type',
            'date_received': 'Date Received',
            'submission_date': 'Submission Date',
            'award_date': 'Award Date',
            'lost_date': 'Lost Date',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # use compact controls
        for name, field in self.fields.items():
            if not isinstance(field.widget, (DateInput,)):
                field.widget.attrs.setdefault('class', 'form-control form-control-sm')

        # client select
        if 'client' in self.fields:
            self.fields['client'].widget.attrs.setdefault('class', 'form-select form-select-sm')

        # leave date fields optional - model.save will set them where appropriate
        self.fields['submission_date'].required = False
        self.fields['award_date'].required = False
        self.fields['lost_date'].required = False

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get('status')
        submission_date = cleaned.get('submission_date')

        if status == 'Submitted' and not submission_date:
            self.add_error('submission_date', 'Submission date is required when status is Submitted.')
        return cleaned