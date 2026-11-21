#include "Abilities/HealthComponent.h"

#include "Abilities/AbilityTypes.h"
#include "Net/UnrealNetwork.h"

UHealthComponent::UHealthComponent()
{
	PrimaryComponentTick.bCanEverTick = false;
	SetIsReplicatedByDefault(true);
}

void UHealthComponent::BeginPlay()
{
	Super::BeginPlay();
	Health = MaxHealth;
	OnHealthChanged.Broadcast(Health, MaxHealth);
}

void UHealthComponent::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
	Super::GetLifetimeReplicatedProps(OutLifetimeProps);
	DOREPLIFETIME(UHealthComponent, Health);
}

void UHealthComponent::OnRep_Health()
{
	OnHealthChanged.Broadcast(Health, MaxHealth);
}

void UHealthComponent::ApplyDamage(float Amount, AActor* DamageInstigator)
{
	if (!GetOwner() || !GetOwner()->HasAuthority() || bDead || Amount <= 0.f)
	{
		return;
	}

	Health = FMath::Max(0.f, Health - Amount);
	OnHealthChanged.Broadcast(Health, MaxHealth);

	UE_LOG(LogSandbox, Log, TEXT("%s took %.0f damage (%.0f/%.0f left)"),
		*GetNameSafe(GetOwner()), Amount, Health, MaxHealth);

	if (Health <= 0.f)
	{
		bDead = true;
		OnDeath.Broadcast(DamageInstigator);
	}
}

void UHealthComponent::ResetHealth()
{
	if (!GetOwner() || !GetOwner()->HasAuthority())
	{
		return;
	}

	bDead = false;
	Health = MaxHealth;
	OnHealthChanged.Broadcast(Health, MaxHealth);
}
