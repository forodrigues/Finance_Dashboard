from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db import transaction as db_transaction

POSITIVE = [MinValueValidator(Decimal('0.01'))]

FUEL_CATEGORY = 'Combustível'
TOLL_CATEGORY = 'Portagens'


def check_same_owner(obj, *fields):
    """Raise if a related record (category, asset) belongs to a different user than `obj`."""
    errors = {}
    for name in fields:
        related_id = getattr(obj, f'{name}_id')
        if obj.user_id and related_id:
            related = getattr(obj, name)
            if related.user_id != obj.user_id:
                errors[name] = 'Este registo pertence a outro utilizador.'
    if errors:
        raise ValidationError(errors)


def owner_field():
    """Every record belongs to one user; views only ever show the logged-in user's records."""
    return models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+', verbose_name='utilizador'
    )


class Category(models.Model):
    INCOME = 'income'
    EXPENSE = 'expense'
    KIND_CHOICES = [
        (INCOME, 'Rendimento'),
        (EXPENSE, 'Despesa'),
    ]

    user = owner_field()
    name = models.CharField('nome', max_length=60)
    kind = models.CharField('tipo', max_length=10, choices=KIND_CHOICES, default=EXPENSE)
    color = models.CharField(
        'cor', max_length=7, default='#6c757d', validators=[RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Cor inválida.')]
    )
    icon = models.CharField('ícone', max_length=40, blank=True, help_text='Nome de um Bootstrap Icon, ex.: cart')

    class Meta:
        ordering = ['kind', 'name']
        verbose_name = 'categoria'
        verbose_name_plural = 'categorias'
        constraints = [models.UniqueConstraint(fields=['user', 'name', 'kind'], name='unique_category_per_user')]

    def __str__(self):
        return self.name


class Transaction(models.Model):
    user = owner_field()
    date = models.DateField('data')
    amount = models.DecimalField('valor', max_digits=10, decimal_places=2, validators=POSITIVE)
    category = models.ForeignKey(
        Category, on_delete=models.RESTRICT, related_name='transactions', verbose_name='categoria'
    )
    description = models.CharField('descrição', max_length=120, blank=True)
    # Set when the transaction was generated from a recurring expense, so generation is idempotent.
    recurring = models.ForeignKey(
        'RecurringExpense', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions'
    )
    # First day of the month the recurring expense was generated for; stays fixed if the date is edited.
    recurring_period = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'transação'
        verbose_name_plural = 'transações'
        constraints = [
            models.UniqueConstraint(fields=['recurring', 'recurring_period'], name='unique_recurring_period')
        ]

    def __str__(self):
        return f'{self.date} {self.category} {self.amount}'

    def clean(self):
        check_same_owner(self, 'category', 'recurring')


class RecurringExpense(models.Model):
    MONTHLY = 'monthly'
    YEARLY = 'yearly'
    FREQUENCY_CHOICES = [(MONTHLY, 'Mensal'), (YEARLY, 'Anual')]

    user = owner_field()
    name = models.CharField('nome', max_length=60)
    amount = models.DecimalField('valor', max_digits=10, decimal_places=2, validators=POSITIVE)
    category = models.ForeignKey(Category, on_delete=models.RESTRICT, verbose_name='categoria')
    frequency = models.CharField('periodicidade', max_length=10, choices=FREQUENCY_CHOICES, default=MONTHLY)
    day_of_month = models.PositiveSmallIntegerField(
        'dia do mês', default=1, validators=[MinValueValidator(1), MaxValueValidator(31)]
    )
    start_date = models.DateField('início')
    end_date = models.DateField('fim', null=True, blank=True)
    active = models.BooleanField('ativa', default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'movimento recorrente'
        verbose_name_plural = 'movimentos recorrentes'

    def __str__(self):
        return self.name

    def clean(self):
        check_same_owner(self, 'category')

    @property
    def monthly_amount(self):
        if self.frequency == self.YEARLY:
            return (self.amount / 12).quantize(Decimal('0.01'))
        return self.amount


class InvestmentAsset(models.Model):
    KIND_CHOICES = [
        ('etf', 'ETF'),
        ('stock', 'Ações'),
        ('crypto', 'Cripto'),
        ('ppr', 'PPR'),
        ('deposit', 'Depósito a prazo'),
        ('bonds', 'Obrigações / Certificados'),
        ('other', 'Outro'),
    ]

    user = owner_field()
    name = models.CharField('nome', max_length=60)
    kind = models.CharField('tipo', max_length=10, choices=KIND_CHOICES, default='etf')

    class Meta:
        ordering = ['name']
        verbose_name = 'ativo'
        verbose_name_plural = 'ativos'
        constraints = [models.UniqueConstraint(fields=['user', 'name'], name='unique_asset_per_user')]

    def __str__(self):
        return self.name


class InvestmentMovement(models.Model):
    BUY = 'buy'
    SELL = 'sell'
    KIND_CHOICES = [(BUY, 'Compra / reforço'), (SELL, 'Venda / resgate')]

    user = owner_field()
    date = models.DateField('data')
    asset = models.ForeignKey(
        InvestmentAsset, on_delete=models.RESTRICT, related_name='movements', verbose_name='ativo'
    )
    kind = models.CharField('tipo', max_length=4, choices=KIND_CHOICES, default=BUY)
    amount = models.DecimalField('valor', max_digits=12, decimal_places=2, validators=POSITIVE)
    note = models.CharField('nota', max_length=120, blank=True)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'movimento de investimento'
        verbose_name_plural = 'movimentos de investimento'

    def __str__(self):
        return f'{self.date} {self.get_kind_display()} {self.asset} {self.amount}'

    def clean(self):
        check_same_owner(self, 'asset')

    @property
    def signed_amount(self):
        return self.amount if self.kind == self.BUY else -self.amount


class InvestmentValuation(models.Model):
    user = owner_field()
    date = models.DateField('data')
    asset = models.ForeignKey(
        InvestmentAsset, on_delete=models.CASCADE, related_name='valuations', verbose_name='ativo'
    )
    value = models.DecimalField('valor atual', max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'avaliação'
        verbose_name_plural = 'avaliações'

    def __str__(self):
        return f'{self.date} {self.asset} {self.value}'

    def clean(self):
        check_same_owner(self, 'asset')


class LinkedTransactionMixin:
    """Keeps a Transaction in sync so car costs show up in the monthly totals."""

    category_name = None

    def transaction_amount(self):
        raise NotImplementedError

    def transaction_description(self):
        raise NotImplementedError

    def default_category(self):
        # Reuse the category of the latest entry so renaming e.g. "Combustível" keeps history together.
        latest = (
            type(self).objects.filter(user=self.user, transaction__isnull=False).select_related('transaction').first()
        )
        if latest:
            return latest.transaction.category
        category, _ = Category.objects.get_or_create(user=self.user, name=self.category_name, kind=Category.EXPENSE)
        return category

    def save(self, *args, **kwargs):
        with db_transaction.atomic():
            tx = self.transaction or Transaction(user=self.user, category=self.default_category())
            if tx.user_id != self.user_id:
                # Owner changed (e.g. in a script): move the expense to the new owner's category too.
                tx.user = self.user
                tx.category = self.default_category()
            tx.date = self.date
            tx.amount = self.transaction_amount()
            tx.description = self.transaction_description()
            tx.save()
            self.transaction = tx
            super().save(*args, **kwargs)


class FuelEntry(LinkedTransactionMixin, models.Model):
    user = owner_field()
    date = models.DateField('data')
    amount = models.DecimalField('valor pago (€)', max_digits=8, decimal_places=2, validators=POSITIVE)
    price_per_liter = models.DecimalField(
        'preço por litro (€/L)', max_digits=6, decimal_places=3, null=True, blank=True, validators=POSITIVE
    )
    # Derived from amount / price_per_liter; unknown when the price was not entered.
    liters = models.DecimalField('litros', max_digits=6, decimal_places=2, null=True, blank=True, editable=False)
    odometer_km = models.PositiveIntegerField('km (conta-quilómetros)', null=True, blank=True)
    full_tank = models.BooleanField('atestei o depósito', default=True)
    station = models.CharField('posto', max_length=60, blank=True)
    transaction = models.OneToOneField(Transaction, on_delete=models.RESTRICT, null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'abastecimento'
        verbose_name_plural = 'abastecimentos'

    def __str__(self):
        return f'{self.date} {self.amount} €'

    def save(self, *args, **kwargs):
        self.compute_liters()
        super().save(*args, **kwargs)

    category_name = FUEL_CATEGORY

    @property
    def total(self):
        return self.amount

    # Largest value FuelEntry.liters can store (max_digits=6, decimal_places=2).
    MAX_LITERS = Decimal('9999.99')

    def clean(self):
        super().clean()
        if self.amount and self.price_per_liter and self.amount / self.price_per_liter > self.MAX_LITERS:
            raise ValidationError({'price_per_liter': 'Preço por litro demasiado baixo para este valor pago.'})

    def compute_liters(self):
        if self.amount is None and self.liters is not None and self.price_per_liter:
            # Entered as litres × price (admin/scripts): derive the amount paid.
            self.amount = (Decimal(self.liters) * Decimal(self.price_per_liter)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
        elif self.amount is not None and self.price_per_liter:
            self.liters = (Decimal(self.amount) / Decimal(self.price_per_liter)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
        else:
            self.liters = None

    def transaction_amount(self):
        return self.amount

    def transaction_description(self):
        text = 'Abastecimento'
        if self.liters is not None:
            text += ' ' + f'{Decimal(self.liters):.2f}'.replace('.', ',') + ' L'
        return text + (f' ({self.station})' if self.station else '')


class TollEntry(LinkedTransactionMixin, models.Model):
    user = owner_field()
    date = models.DateField('data')
    amount = models.DecimalField('valor', max_digits=8, decimal_places=2, validators=POSITIVE)
    description = models.CharField('descrição', max_length=120, blank=True)
    transaction = models.OneToOneField(Transaction, on_delete=models.RESTRICT, null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'portagem'
        verbose_name_plural = 'portagens'

    category_name = TOLL_CATEGORY

    def __str__(self):
        return f'{self.date} {self.amount}'

    def transaction_amount(self):
        return self.amount

    def transaction_description(self):
        return self.description or 'Portagem'


class CommuteDay(models.Model):
    """A day the user drove to work. Only presence matters; used for counting."""

    user = owner_field()
    date = models.DateField('data')

    class Meta:
        ordering = ['-date']
        verbose_name = 'ida de carro'
        verbose_name_plural = 'idas de carro'
        constraints = [models.UniqueConstraint(fields=['user', 'date'], name='unique_commute_per_user')]

    def __str__(self):
        return str(self.date)
