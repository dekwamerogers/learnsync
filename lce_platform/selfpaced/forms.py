from datetime import date

from django import forms

from selfpaced.parsing import load_csv, validate_columns


class CSVUploadForm(forms.Form):
    file = forms.FileField(
        label='CSV file',
        help_text='Upload the self-paced assignment-level CSV exported from the staff portal.',
    )
    data_as_of_date = forms.DateField(
        label='Data as of date',
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text=(
            'The date this CSV was exported from the source system. '
            'Health flags (days inactive, days since FSOL, etc.) are calculated '
            'relative to this date. Leave blank to use today.'
        ),
        initial=date.today,
    )

    def clean_file(self):
        f = self.cleaned_data['file']
        if not f.name.lower().endswith('.csv'):
            raise forms.ValidationError('Only CSV files are accepted.')
        # TODO: re-enable size limit once large uploads are no longer needed
        # if f.size > 50 * 1024 * 1024:
        #     raise forms.ValidationError('File is too large (max 50 MB).')

        content = f.read()
        headers, _ = load_csv(content)
        errors = validate_columns(headers)
        if errors:
            raise forms.ValidationError(errors)

        # Rewind so the view can read it again
        f.seek(0)
        return f
