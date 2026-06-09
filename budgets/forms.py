from django import forms
from decimal import Decimal, InvalidOperation

from .models import Piece, ServiceCatalog

class CiliaXMLUploadForm(forms.Form):
    xml_file = forms.FileField(label='Arquivo XML (Cilia)')

    def clean_xml_file(self):
        xml_file = self.cleaned_data['xml_file']

        if xml_file.size > 10 * 1024 * 1024:
            raise forms.ValidationError('O arquivo deve ter no máximo 10MB.')

        if not xml_file.name.lower().endswith('.xml'):
            raise forms.ValidationError('Envie um arquivo .xml.')

        return xml_file


class ThirdPartyServiceForm(forms.Form):
    description = forms.CharField(label='Descrição', max_length=255)
    amount = forms.CharField(label='Valor (R$)', max_length=32)

    def clean_amount(self):
        raw = (self.cleaned_data.get('amount') or '').strip()
        raw = raw.replace('R$', '').strip()
        raw = raw.replace(' ', '')
        raw = raw.replace('.', '')
        raw = raw.replace(',', '.')
        try:
            value = Decimal(raw)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError('Informe um valor válido.')
        if value < 0:
            raise forms.ValidationError('O valor não pode ser negativo.')
        return value


class ServiceCatalogForm(forms.ModelForm):
    def clean(self):
        cleaned = super().clean()
        default_value = cleaned.get('default_value')
        commission_mode = cleaned.get('commission_mode')
        commission_value = cleaned.get('commission_value')

        if default_value is not None and default_value < 0:
            self.add_error('default_value', 'O valor padrão não pode ser negativo.')

        if commission_value is None:
            commission_value = Decimal('0')
            cleaned['commission_value'] = commission_value

        if commission_value < 0:
            self.add_error('commission_value', 'O valor de comissão não pode ser negativo.')

        if commission_mode == ServiceCatalog.CommissionMode.PERCENT and commission_value > 100:
            self.add_error('commission_value', 'Para % sobre valor, informe um percentual entre 0 e 100.')

        if commission_mode == ServiceCatalog.CommissionMode.NONE:
            cleaned['commission_value'] = Decimal('0')

        return cleaned

    class Meta:
        model = ServiceCatalog
        fields = ('name', 'default_value', 'commission_mode', 'commission_value')


class PieceForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cost_price'].required = False
        self.fields['profit_percent'].required = False

    def clean(self):
        cleaned = super().clean()
        provider_type = cleaned.get('provider_type')
        cost_price = cleaned.get('cost_price')
        profit_percent = cleaned.get('profit_percent')

        if provider_type == Piece.ProviderType.SHOP:
            if cost_price is None:
                self.add_error('cost_price', 'Informe o valor da peça (custo) quando a fornecedora for a oficina.')
        else:
            cleaned['cost_price'] = Decimal('0')
            cleaned['profit_percent'] = Decimal('0')

        if profit_percent is None:
            cleaned['profit_percent'] = Decimal('0')

        return cleaned

    class Meta:
        model = Piece
        fields = (
            'name',
            'provider_type',
            'purchase_date',
            'expected_arrival_date',
            'arrival_date',
            'arrived',
            'cost_price',
            'profit_percent',
        )
